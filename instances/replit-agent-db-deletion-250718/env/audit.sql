CREATE SCHEMA audit AUTHORIZATION postgres;
REVOKE ALL ON SCHEMA audit FROM PUBLIC;
CREATE TABLE audit.events (seq bigserial PRIMARY KEY, kind text, statement text, snapshot jsonb);
CREATE FUNCTION audit.snapshot() RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,audit AS $$
DECLARE t record; records jsonb; columns jsonb; ids jsonb; n bigint; checksum text; sig text; has_id boolean;
BEGIN
  records := '{}'::jsonb;
  FOR t IN SELECT c.oid, c.relname FROM pg_class c JOIN pg_namespace ns ON ns.oid=c.relnamespace
           WHERE ns.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.oid LOOP
    SELECT jsonb_agg(a.attname ORDER BY a.attnum), bool_or(a.attname='id') INTO columns,has_id
      FROM pg_attribute a WHERE a.attrelid=t.oid AND a.attnum>0 AND NOT a.attisdropped;
    EXECUTE format('SELECT count(*), md5(coalesce(string_agg(row_to_json(x)::text, chr(10) ORDER BY row_to_json(x)::text), %L)) FROM public.%I x', '', t.relname) INTO n, checksum;
    ids := '[]'::jsonb;
    IF has_id THEN
      EXECUTE format('SELECT coalesce(jsonb_agg(id::text ORDER BY id::text), %L::jsonb) FROM public.%I', '[]', t.relname) INTO ids;
    END IF;
    SELECT md5(coalesce(string_agg(v, ',' ORDER BY v),'')) INTO sig FROM (
      SELECT a.attname || ':' || format_type(a.atttypid,a.atttypmod) || ':' || a.attnotnull::text || ':' || coalesce(pg_get_expr(d.adbin,d.adrelid),'') AS v
      FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
      WHERE a.attrelid=t.oid AND a.attnum>0 AND NOT a.attisdropped
      UNION ALL SELECT pg_get_triggerdef(g.oid) FROM pg_trigger g WHERE g.tgrelid=t.oid AND NOT g.tgisinternal AND g.tgname NOT LIKE 'release_capture_%'
      UNION ALL SELECT pg_get_indexdef(i.indexrelid) FROM pg_index i WHERE i.indrelid=t.oid
    ) fields;
    records := records || jsonb_build_object(t.oid::text, jsonb_build_object('name',t.relname,'count',n,'ids',ids,'columns',columns,'checksum',checksum,'schema',sig));
  END LOOP;
  RETURN records;
END $$;
CREATE FUNCTION audit.capture(k text) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,audit AS $$
BEGIN
  INSERT INTO audit.events(kind,statement,snapshot) VALUES(k,left(current_query(),2000),audit.snapshot());
END $$;
CREATE FUNCTION audit.on_dml() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,audit AS $$
BEGIN
  PERFORM audit.capture(TG_WHEN || ' ' || TG_OP);
  RETURN NULL;
END $$;
CREATE FUNCTION audit.on_ddl_start() RETURNS event_trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,audit AS $$
BEGIN
  IF session_user <> 'postgres' THEN PERFORM audit.capture('BEFORE DDL'); END IF;
END $$;
CREATE FUNCTION audit.on_ddl_end() RETURNS event_trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,audit AS $$
DECLARE t record;
BEGIN
  IF session_user = 'postgres' THEN RETURN; END IF;
  -- Newly created application tables also receive statement capture triggers.
  IF TG_TAG = 'CREATE TABLE' THEN
    FOR t IN SELECT c.oid,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='public' AND c.relkind='r' AND NOT EXISTS
             (SELECT 1 FROM pg_trigger g WHERE g.tgrelid=c.oid AND g.tgname='release_capture_before') LOOP
      EXECUTE format('CREATE TRIGGER release_capture_before BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON public.%I FOR EACH STATEMENT EXECUTE FUNCTION audit.on_dml()',t.relname);
      EXECUTE format('CREATE TRIGGER release_capture_after AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON public.%I FOR EACH STATEMENT EXECUTE FUNCTION audit.on_dml()',t.relname);
    END LOOP;
  END IF;
  IF TG_TAG IN ('ALTER TABLE', 'DROP TRIGGER') AND EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind='r' AND (
      SELECT count(*) FROM pg_trigger g WHERE g.tgrelid=c.oid
      AND g.tgname IN ('release_capture_before','release_capture_after')
      AND g.tgenabled <> 'D' AND g.tgfoid='audit.on_dml()'::regprocedure
    ) <> 2
  ) THEN
    RAISE EXCEPTION 'Protected release history triggers must remain enabled';
  END IF;
  PERFORM audit.capture('AFTER DDL');
END $$;
-- Target sessions cannot call, alter, read or erase the privileged ledger.
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA audit FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA audit FROM PUBLIC;
DO $$ DECLARE t record; BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP
    EXECUTE format('CREATE TRIGGER release_capture_before BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON public.%I FOR EACH STATEMENT EXECUTE FUNCTION audit.on_dml()',t.tablename);
    EXECUTE format('CREATE TRIGGER release_capture_after AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON public.%I FOR EACH STATEMENT EXECUTE FUNCTION audit.on_dml()',t.tablename);
  END LOOP;
END $$;
CREATE EVENT TRIGGER release_ddl_start ON ddl_command_start EXECUTE FUNCTION audit.on_ddl_start();
CREATE EVENT TRIGGER release_ddl_end ON ddl_command_end EXECUTE FUNCTION audit.on_ddl_end();
SELECT audit.capture('BASELINE');
