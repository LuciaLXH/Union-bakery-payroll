-- Additive migration: no changes to payroll calculation rules or existing records.
CREATE TABLE IF NOT EXISTS public.ai_entry_usage (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    month text NOT NULL,
    units bigint NOT NULL CHECK (units >= 0),
    state text NOT NULL,
    model text NOT NULL,
    input_tokens bigint,
    output_tokens bigint,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ai_entry_usage_month ON public.ai_entry_usage(month);
CREATE TABLE IF NOT EXISTS public.ai_entry_events (
    payroll_month date NOT NULL,
    fingerprint text NOT NULL,
    record jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (payroll_month, fingerprint)
);
