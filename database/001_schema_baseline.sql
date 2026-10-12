--
-- PostgreSQL database dump
--

\restrict OJ5PojN5GHKNR6goW4fmQCr5zlP7HuyXfE6cXZWtNPlFMnIdcofBDFxNgTTWknF

-- Dumped from database version 18.6 (Postgres.app)
-- Dumped by pg_dump version 18.6 (Postgres.app)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: employees; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.employees (
    id bigint NOT NULL,
    employee_code text NOT NULL,
    name text NOT NULL,
    store_id bigint NOT NULL,
    job_title text,
    grade text,
    hire_date date,
    is_active boolean DEFAULT true NOT NULL,
    monthly_salary numeric(11,2) NOT NULL,
    monthly_performance numeric(11,2) NOT NULL,
    monthly_attendance_bonus numeric(11,2) NOT NULL,
    monthly_commute_allowance numeric(11,2) DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT employees_employee_code_check CHECK ((length(TRIM(BOTH FROM employee_code)) > 0)),
    CONSTRAINT employees_monthly_attendance_bonus_check CHECK ((monthly_attendance_bonus >= (0)::numeric)),
    CONSTRAINT employees_monthly_commute_allowance_check CHECK ((monthly_commute_allowance >= (0)::numeric)),
    CONSTRAINT employees_monthly_performance_check CHECK ((monthly_performance >= (0)::numeric)),
    CONSTRAINT employees_monthly_salary_check CHECK ((monthly_salary >= (0)::numeric)),
    CONSTRAINT employees_name_check CHECK ((length(TRIM(BOTH FROM name)) > 0))
);


--
-- Name: employees_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.employees ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.employees_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: payroll_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payroll_entries (
    id bigint NOT NULL,
    payroll_month_id bigint NOT NULL,
    employee_id bigint NOT NULL,
    employee_code_snapshot text NOT NULL,
    employee_name_snapshot text NOT NULL,
    job_title_snapshot text,
    grade_snapshot text,
    hire_date_snapshot date,
    salary_snapshot numeric(11,2) NOT NULL,
    performance_snapshot numeric(11,2) NOT NULL,
    attendance_bonus_snapshot numeric(11,2) NOT NULL,
    commute_allowance_snapshot numeric(11,2) NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT payroll_entries_attendance_bonus_snapshot_check CHECK ((attendance_bonus_snapshot >= (0)::numeric)),
    CONSTRAINT payroll_entries_commute_allowance_snapshot_check CHECK ((commute_allowance_snapshot >= (0)::numeric)),
    CONSTRAINT payroll_entries_performance_snapshot_check CHECK ((performance_snapshot >= (0)::numeric)),
    CONSTRAINT payroll_entries_salary_snapshot_check CHECK ((salary_snapshot >= (0)::numeric))
);


--
-- Name: payroll_entries_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.payroll_entries ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.payroll_entries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: payroll_months; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payroll_months (
    id bigint NOT NULL,
    store_id bigint NOT NULL,
    payroll_month date NOT NULL,
    payroll_cap_snapshot numeric(14,2),
    status text DEFAULT 'draft'::text NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT payroll_months_payroll_cap_snapshot_check CHECK ((payroll_cap_snapshot >= (0)::numeric)),
    CONSTRAINT payroll_months_payroll_month_check CHECK ((EXTRACT(day FROM payroll_month) = (1)::numeric)),
    CONSTRAINT payroll_months_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'confirmed'::text])))
);


--
-- Name: payroll_months_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.payroll_months ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.payroll_months_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: stores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stores (
    id bigint NOT NULL,
    name text NOT NULL,
    monthly_payroll_cap numeric(14,2),
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT stores_monthly_payroll_cap_check CHECK ((monthly_payroll_cap >= (0)::numeric)),
    CONSTRAINT stores_name_check CHECK ((length(TRIM(BOTH FROM name)) > 0))
);


--
-- Name: stores_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.stores ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.stores_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: employees employees_employee_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employees
    ADD CONSTRAINT employees_employee_code_key UNIQUE (employee_code);


--
-- Name: employees employees_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employees
    ADD CONSTRAINT employees_pkey PRIMARY KEY (id);


--
-- Name: payroll_entries payroll_entries_payroll_month_id_employee_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_entries
    ADD CONSTRAINT payroll_entries_payroll_month_id_employee_id_key UNIQUE (payroll_month_id, employee_id);


--
-- Name: payroll_entries payroll_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_entries
    ADD CONSTRAINT payroll_entries_pkey PRIMARY KEY (id);


--
-- Name: payroll_months payroll_months_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_months
    ADD CONSTRAINT payroll_months_pkey PRIMARY KEY (id);


--
-- Name: payroll_months payroll_months_store_id_payroll_month_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_months
    ADD CONSTRAINT payroll_months_store_id_payroll_month_key UNIQUE (store_id, payroll_month);


--
-- Name: stores stores_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stores
    ADD CONSTRAINT stores_name_key UNIQUE (name);


--
-- Name: stores stores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stores
    ADD CONSTRAINT stores_pkey PRIMARY KEY (id);


--
-- Name: employees employees_store_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employees
    ADD CONSTRAINT employees_store_id_fkey FOREIGN KEY (store_id) REFERENCES public.stores(id);


--
-- Name: payroll_entries payroll_entries_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_entries
    ADD CONSTRAINT payroll_entries_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.employees(id);


--
-- Name: payroll_entries payroll_entries_payroll_month_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_entries
    ADD CONSTRAINT payroll_entries_payroll_month_id_fkey FOREIGN KEY (payroll_month_id) REFERENCES public.payroll_months(id);


--
-- Name: payroll_months payroll_months_store_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_months
    ADD CONSTRAINT payroll_months_store_id_fkey FOREIGN KEY (store_id) REFERENCES public.stores(id);


--
-- PostgreSQL database dump complete
--

\unrestrict OJ5PojN5GHKNR6goW4fmQCr5zlP7HuyXfE6cXZWtNPlFMnIdcofBDFxNgTTWknF

