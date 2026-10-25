-- Additive migration. Apply once with the database owner, before this release.
CREATE TABLE public.app_environment (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    environment text NOT NULL CHECK (environment IN ('test', 'production')),
    instance_id text NOT NULL
);
INSERT INTO public.app_environment VALUES (true, 'test', 'unassigned-test');

CREATE TABLE public.owner_login_attempts (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    failures integer NOT NULL DEFAULT 0,
    blocked_until timestamptz
);
INSERT INTO public.owner_login_attempts(singleton) VALUES (true);

ALTER TABLE public.payroll_months
    DROP CONSTRAINT payroll_months_status_check,
    ADD CONSTRAINT payroll_months_status_check CHECK (status IN ('draft','confirmed','paid')),
    ADD COLUMN lifecycle_version bigint NOT NULL DEFAULT 1,
    ADD COLUMN confirmed_rule_version text,
    ADD COLUMN confirmed_calculation_ids bigint[],
    ADD COLUMN confirmed_at timestamptz,
    ADD COLUMN confirmed_by text,
    ADD COLUMN paid_at timestamptz,
    ADD CONSTRAINT payroll_months_confirmation_check CHECK (
        (status='draft' AND confirmed_rule_version IS NULL AND confirmed_calculation_ids IS NULL
         AND confirmed_at IS NULL AND confirmed_by IS NULL AND paid_at IS NULL)
        OR (status IN ('confirmed','paid') AND confirmed_rule_version IS NOT NULL
            AND cardinality(confirmed_calculation_ids)>0 AND confirmed_at IS NOT NULL
            AND confirmed_by IS NOT NULL AND ((status='paid') = (paid_at IS NOT NULL)))
    );

CREATE TABLE public.audit_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    happened_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    actor text NOT NULL,
    database_user text NOT NULL,
    table_name text NOT NULL,
    record_id bigint NOT NULL,
    operation text NOT NULL,
    reason text NOT NULL DEFAULT '',
    before_record jsonb,
    after_record jsonb
);

CREATE FUNCTION public.record_payroll_audit() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF TG_OP='UPDATE' AND to_jsonb(OLD)=to_jsonb(NEW) THEN RETURN NEW; END IF;
    INSERT INTO public.audit_log(actor,database_user,table_name,record_id,operation,reason,before_record,after_record)
    VALUES (COALESCE(NULLIF(current_setting('app.actor',true),''),'maintenance'),
        session_user,TG_TABLE_NAME,COALESCE(NEW.id,OLD.id),TG_OP,
        COALESCE(current_setting('app.reason',true),''),
        CASE WHEN TG_OP<>'INSERT' THEN to_jsonb(OLD) END,
        CASE WHEN TG_OP<>'DELETE' THEN to_jsonb(NEW) END);
    RETURN COALESCE(NEW,OLD);
END;
$$;

CREATE FUNCTION public.guard_payroll_entry() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE month_status text;
BEGIN
    IF TG_OP='UPDATE' AND NEW.payroll_month_id<>OLD.payroll_month_id THEN
        RAISE EXCEPTION 'Monthly entries cannot move between months';
    END IF;
    SELECT status INTO month_status FROM public.payroll_months
    WHERE id=CASE WHEN TG_OP='DELETE' THEN OLD.payroll_month_id ELSE NEW.payroll_month_id END FOR UPDATE;
    IF month_status IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION 'Confirmed payroll is locked';
    END IF;
    RETURN COALESCE(NEW,OLD);
END;
$$;
CREATE TRIGGER payroll_entries_lock BEFORE INSERT OR UPDATE OR DELETE ON public.payroll_entries
FOR EACH ROW EXECUTE FUNCTION public.guard_payroll_entry();

CREATE FUNCTION public.guard_payroll_calculation() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE month_status text;
BEGIN
    IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Calculation history is immutable'; END IF;
    SELECT p.status INTO month_status FROM public.payroll_months p
    JOIN public.payroll_entries e ON e.payroll_month_id=p.id
    WHERE e.id=NEW.payroll_entry_id FOR UPDATE OF p;
    IF month_status IS DISTINCT FROM 'draft' THEN RAISE EXCEPTION 'Confirmed payroll is locked'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER payroll_calculations_lock BEFORE INSERT OR UPDATE OR DELETE ON public.payroll_calculations
FOR EACH ROW EXECUTE FUNCTION public.guard_payroll_calculation();

CREATE FUNCTION public.guard_payroll_month() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE entry_count integer; valid_count integer; reason text;
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.status<>'draft' THEN RAISE EXCEPTION 'New months must be drafts'; END IF;
        RETURN NEW;
    END IF;
    IF NEW.store_id<>OLD.store_id OR NEW.payroll_month<>OLD.payroll_month THEN
        RAISE EXCEPTION 'Month identity is immutable';
    END IF;
    NEW.lifecycle_version:=OLD.lifecycle_version;
    IF NEW.status=OLD.status THEN
        IF (to_jsonb(NEW)-'payroll_cap_snapshot') IS DISTINCT FROM (to_jsonb(OLD)-'payroll_cap_snapshot')
           OR (OLD.status<>'draft' AND NEW.payroll_cap_snapshot IS DISTINCT FROM OLD.payroll_cap_snapshot) THEN
            RAISE EXCEPTION 'Confirmed metadata is immutable';
        END IF;
        RETURN NEW;
    END IF;
    reason:=NULLIF(trim(current_setting('app.reason',true)), '');
    IF reason IS NULL THEN RAISE EXCEPTION 'A reason is required for payroll transitions'; END IF;
    IF NEW.status='confirmed' AND OLD.status='draft' THEN
        PERFORM id FROM public.payroll_entries WHERE payroll_month_id=OLD.id ORDER BY id FOR UPDATE;
        SELECT count(*) INTO entry_count FROM public.payroll_entries WHERE payroll_month_id=OLD.id;
        SELECT count(DISTINCT e.id) INTO valid_count FROM public.payroll_entries e
        JOIN public.payroll_calculations c ON c.payroll_entry_id=e.id
        WHERE e.payroll_month_id=OLD.id AND c.id=ANY(NEW.confirmed_calculation_ids)
          AND c.input_version=e.input_version AND c.rule_version=NEW.confirmed_rule_version AND c.net_pay>=0;
        IF entry_count=0 OR valid_count<>entry_count OR cardinality(NEW.confirmed_calculation_ids)<>entry_count THEN
            RAISE EXCEPTION 'All employees need current nonnegative calculations';
        END IF;
        NEW.confirmed_at:=clock_timestamp();
        NEW.confirmed_by:=COALESCE(NULLIF(current_setting('app.actor',true),''),'maintenance');
        NEW.paid_at:=NULL;
    ELSIF NEW.status='paid' AND OLD.status='confirmed' THEN
        NEW.confirmed_at:=OLD.confirmed_at; NEW.confirmed_by:=OLD.confirmed_by;
        NEW.confirmed_rule_version:=OLD.confirmed_rule_version;
        NEW.confirmed_calculation_ids:=OLD.confirmed_calculation_ids;
        NEW.paid_at:=clock_timestamp();
    ELSIF NEW.status='draft' AND OLD.status IN ('confirmed','paid') THEN
        NEW.confirmed_at:=NULL; NEW.confirmed_by:=NULL;
        NEW.confirmed_rule_version:=NULL; NEW.confirmed_calculation_ids:=NULL; NEW.paid_at:=NULL;
        -- Entries are untouched; original confirmed IDs remain in the audit log.
    ELSE
        RAISE EXCEPTION 'Invalid payroll transition';
    END IF;
    IF NEW.payroll_cap_snapshot IS DISTINCT FROM OLD.payroll_cap_snapshot THEN
        RAISE EXCEPTION 'Change cap separately before confirmation';
    END IF;
    NEW.lifecycle_version:=OLD.lifecycle_version+1;
    RETURN NEW;
END;
$$;
CREATE TRIGGER payroll_months_guard BEFORE INSERT OR UPDATE ON public.payroll_months
FOR EACH ROW EXECUTE FUNCTION public.guard_payroll_month();

CREATE TRIGGER stores_audit AFTER INSERT OR UPDATE OR DELETE ON public.stores
FOR EACH ROW EXECUTE FUNCTION public.record_payroll_audit();
CREATE TRIGGER employees_audit AFTER INSERT OR UPDATE OR DELETE ON public.employees
FOR EACH ROW EXECUTE FUNCTION public.record_payroll_audit();
CREATE TRIGGER payroll_entries_audit AFTER INSERT OR UPDATE OR DELETE ON public.payroll_entries
FOR EACH ROW EXECUTE FUNCTION public.record_payroll_audit();
CREATE TRIGGER payroll_months_audit AFTER INSERT OR UPDATE OR DELETE ON public.payroll_months
FOR EACH ROW EXECUTE FUNCTION public.record_payroll_audit();
CREATE TRIGGER payroll_calculations_audit AFTER INSERT ON public.payroll_calculations
FOR EACH ROW EXECUTE FUNCTION public.record_payroll_audit();
REVOKE ALL ON FUNCTION public.record_payroll_audit() FROM PUBLIC;
