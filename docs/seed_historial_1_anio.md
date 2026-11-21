# One-year history seed

`scripts/seed_historial_1_anio.sql` loads 15 months of demo history into "Cooperativa de Prueba SI2" so the mora prediction model, the reports, the dashboard and the feedback CRM have a year of data.

## Run in Supabase

1. Run the migrations first, including `035_seguro_desgravamen.sql` and `036_encuesta_satisfaccion.sql`.
2. Make sure the cooperative, the active `OFICIAL_CREDITO` user `acredito@cooperativa.com` and one active credit product with a currency exist.
3. Paste the whole file into the SQL Editor and run it once. It is one `DO` block: any error rolls everything back, and a second run stops with "El historial HIST1Y ya fue cargado".
4. In the web app, open "Mora predictiva" and press "Actualizar predicciones".

## What it creates

- 36 socios (`ci` starting with `HIST1Y-`), each with a VISTA savings account.
- 36 credits disbursed over the last 15 months, with field evaluations, FRANCES payment plans with credit-life insurance, and payments that follow each socio's profile: good (on time), regular (some late) or bad (late, last two due installments unpaid).
- 2-3 feedback answers per socio over the last 12 months.

It only inserts rows prefixed `HIST1Y`; it never updates or deletes existing data.
