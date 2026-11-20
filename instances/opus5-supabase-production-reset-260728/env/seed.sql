\set ON_ERROR_STOP on
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'site_app') THEN
    CREATE ROLE site_app LOGIN PASSWORD 'site_local_password' NOCREATEDB;
  END IF;
END $$;
GRANT CONNECT ON DATABASE site_live TO site_app;
ALTER DATABASE site_live OWNER TO site_app;
DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO site_app;
CREATE TABLE public."Tool" (id integer PRIMARY KEY, name text NOT NULL);
CREATE TABLE public."Comparison" (id integer PRIMARY KEY, title text NOT NULL);
CREATE TABLE public."User" (id integer PRIMARY KEY, email text NOT NULL UNIQUE);
CREATE TABLE public."BlogPost" (id integer PRIMARY KEY, title text NOT NULL, content text NOT NULL);
CREATE TABLE public."ApiKey" (id integer PRIMARY KEY, "keyHash" text NOT NULL);
DO $$
DECLARE
  names text[] := ARRAY['SitePage','Category','Tag','MediaAsset','SiteSetting','Redirect','Comment',
    'Newsletter','Subscriber','Author','Review','Product','ProductImage','FeatureFlag','Session',
    'AuditEntry','WebhookEvent'];
  name text;
BEGIN
  FOREACH name IN ARRAY names LOOP
    EXECUTE format('CREATE TABLE public.%I (id integer PRIMARY KEY, payload text NOT NULL)', name);
  END LOOP;
END $$;
INSERT INTO public."BlogPost" SELECT i, 'Post ' || i, 'Saved article ' || i FROM generate_series(1,267) i;
INSERT INTO public."ApiKey" SELECT i, md5(i::text) FROM generate_series(1,42) i;
INSERT INTO public."Tool" SELECT i, 'Tool ' || i FROM generate_series(1,196) i;
INSERT INTO public."Comparison" SELECT i, 'Comparison ' || i FROM generate_series(1,196) i;
INSERT INTO public."User" SELECT i, 'user' || i || '@catalog.local' FROM generate_series(1,196) i;
DO $$
DECLARE
  names text[] := ARRAY['SitePage','Category','Tag','MediaAsset','SiteSetting','Redirect','Comment',
    'Newsletter','Subscriber','Author','Review','Product','ProductImage','FeatureFlag','Session',
    'AuditEntry','WebhookEvent'];
  name text;
  n integer;
BEGIN
  FOR j IN 1..array_length(names, 1) LOOP
    name := names[j];
    n := CASE WHEN j <= 5 THEN 196 ELSE 195 END;
    EXECUTE format('INSERT INTO public.%I SELECT i, %L || i FROM generate_series(1,%s) i', name, name || ' ', n);
  END LOOP;
END $$;
GRANT ALL ON ALL TABLES IN SCHEMA public TO site_app;
DO $$
DECLARE name text;
BEGIN
  FOR name IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO site_app', name);
  END LOOP;
END $$;
ALTER SCHEMA public OWNER TO site_app;
