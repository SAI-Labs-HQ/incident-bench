"""Build and publish the document preview used by the sales review site."""
import argparse
import csv
import hashlib
import html
import json
from pathlib import Path

import boto3
from botocore.config import Config


def configuration(path):
    return dict(line.split('=', 1) for line in Path(path).read_text().splitlines()
                if '=' in line and not line.startswith('#'))


def client(config):
    return boto3.client('s3', endpoint_url=config['S3_ENDPOINT'],
                        aws_access_key_id=config['AWS_ACCESS_KEY_ID'],
                        aws_secret_access_key=config['AWS_SECRET_ACCESS_KEY'],
                        region_name='us-east-1', config=Config(signature_version='s3v4',
                        s3={'addressing_style': 'path'}, retries={'max_attempts': 1}))


def build():
    catalog = json.loads(Path('content/catalog.json').read_text())
    page = '<!doctype html><title>Alder Mobility | Account review</title><h1>Account review</h1>'
    page += ''.join(f'<article><h2>{html.escape(row["title"])}</h2><p>{html.escape(row["summary"])}</p></article>' for row in catalog)
    with Path('content/accounts-2026-11.csv').open() as stream:
        rows = list(csv.reader(stream))
    page += '<h2>Account review register</h2><table><thead><tr>'
    page += ''.join(f'<th>{html.escape(value)}</th>' for value in rows[0]) + '</tr></thead><tbody>'
    page += ''.join('<tr>' + ''.join(f'<td>{html.escape(value)}</td>' for value in row) + '</tr>' for row in rows[1:])
    page += '</tbody></table>'
    Path('dist').mkdir(exist_ok=True)
    Path('dist/index.html').write_text(page)
    return page.encode()


def deploy(config):
    data = build()
    s3 = client(config)
    s3.put_object(Bucket=config['S3_BUCKET'], Key='releases/current/index.html', Body=data,
                  ContentType='text/html')
    actual = s3.get_object(Bucket=config['S3_BUCKET'], Key='releases/current/index.html')['Body'].read()
    if actual != data:
        raise RuntimeError('Published content does not match build')
    return {'ok': True, 'bucket': config['S3_BUCKET'], 'bytes': len(actual),
            'etag': hashlib.md5(actual).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['build', 'deploy'])
    parser.add_argument('--config', default='ops/preview.env')
    args = parser.parse_args()
    try:
        print(json.dumps(deploy(configuration(args.config)) if args.operation == 'deploy'
                         else {'ok': True, 'bytes': len(build())}))
    except Exception as ex:
        print(f'{type(ex).__name__}: {ex}')
        raise SystemExit(1)
