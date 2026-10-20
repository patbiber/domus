#!/usr/bin/env bash
# Löscht Statistikdaten, die älter als 25 Monate sind (Versprechen in der Datenschutzerklärung, homi.solar/datenschutz.html).
# Läuft monatlich (umami-aufraeumen.timer). Tabellen, die es in einer Umami-Version nicht gibt, werden übersprungen.
set -euo pipefail
docker exec -i umami-db psql -U umami -d umami -v ON_ERROR_STOP=1 -q <<'SQL'
DO $$
DECLARE t text; n bigint;
BEGIN
  FOREACH t IN ARRAY ARRAY['event_data','session_data','heatmap_event','revenue','session_replay','website_event'] LOOP
    IF to_regclass('public.' || t) IS NOT NULL THEN
      EXECUTE format('DELETE FROM %I WHERE created_at < now() - interval ''25 months''', t);
      GET DIAGNOSTICS n = ROW_COUNT;
      RAISE NOTICE '% : % Zeilen gelöscht', t, n;
    END IF;
  END LOOP;
  DELETE FROM session s WHERE s.created_at < now() - interval '25 months'
    AND NOT EXISTS (SELECT 1 FROM website_event e WHERE e.session_id = s.session_id);
  GET DIAGNOSTICS n = ROW_COUNT;
  RAISE NOTICE 'session : % Zeilen gelöscht', n;
END $$;
SQL
