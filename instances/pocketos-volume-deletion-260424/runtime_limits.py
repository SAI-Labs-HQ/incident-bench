"""Cost-aware Inspect limits shared by real lanes (prices from the core registry)."""
from pathlib import Path

import yaml
from inspect_ai.model import GenerateInput, ModelCost, ModelInfo, get_model_info, set_model_info
from ib import config, models, pricing


def real_limits():
    # Inspect needs prices before starting the task. Register aliases plus the incident
    # subjects; raw unregistered targets can supply Inspect's --model-cost-config.
    manifest = yaml.safe_load((Path(__file__).parent / 'manifest.yaml').read_text())
    specs = set(models.aliases()) | set(manifest['subjects']['incident_models'])
    specs |= {config.get(f'models.{role}') for role in ('target', 'judge', 'auditor', 'judge_fallback')}
    for name in specs - {None}:
        try:
            spec = models.resolve(name)
            price = pricing.price_for(spec)
        except KeyError:
            continue
        identifier = 'mockllm/model' if spec.provider == 'mock' else spec.inspect_name
        info = get_model_info(identifier) or ModelInfo()
        info.cost = ModelCost(input=price.input * 1e6, output=price.output * 1e6,
                                 input_cache_read=(price.cached_input if price.cached_input is not None else price.input) * 1e6,
                                 input_cache_write=(price.cache_write if price.cache_write is not None else price.input) * 1e6)
        set_model_info(identifier, info)
    # Cost is checked between API calls: reserve headroom for the final call and judge.
    return dict(message_limit=100, token_limit=1_500_000, time_limit=1200, cost_limit=0.65)


async def bounded_response(model, input, tools, tool_choice, config):
    """Bound a native request's maximum overshoot beyond the between-call cost stop."""
    updates = {'max_tokens': min(config.max_tokens or 4096, 4096)}
    if config.reasoning_tokens and config.reasoning_tokens > 3072:
        updates['reasoning_tokens'] = 3072
    config = config.model_copy(update=updates)
    return GenerateInput(input=input, tools=tools, tool_choice=tool_choice, config=config)
