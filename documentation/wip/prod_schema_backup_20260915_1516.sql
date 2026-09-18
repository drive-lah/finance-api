--
-- PostgreSQL database dump
--

\restrict AYl8Mdm7p14ecaYT0RqdB6LrGmVJeiLKnW77cQgO1WlNa9kIMz8ufkp5i1Wcr9J

-- Dumped from database version 17.9
-- Dumped by pg_dump version 17.10 (Homebrew)

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
-- Name: finance_coa_config; Type: TABLE; Schema: public; Owner: collectionsagent
--

CREATE TABLE public.finance_coa_config (
    id integer NOT NULL,
    coa_code character varying(32) NOT NULL,
    approval_threshold_sgd numeric(15,2),
    approver_1 character varying(255),
    approver_2 character varying(255),
    second_approver_above_sgd numeric(15,2),
    auto_approve_ok boolean DEFAULT false NOT NULL,
    needs_trip_id boolean DEFAULT false NOT NULL,
    needs_intercom_id boolean DEFAULT false NOT NULL,
    other_required text,
    notes text,
    updated_by character varying(255),
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.finance_coa_config OWNER TO collectionsagent;

--
-- Name: finance_coa_config_id_seq; Type: SEQUENCE; Schema: public; Owner: collectionsagent
--

CREATE SEQUENCE public.finance_coa_config_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.finance_coa_config_id_seq OWNER TO collectionsagent;

--
-- Name: finance_coa_config_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: collectionsagent
--

ALTER SEQUENCE public.finance_coa_config_id_seq OWNED BY public.finance_coa_config.id;


--
-- Name: finance_payouts; Type: TABLE; Schema: public; Owner: collectionsagent
--

CREATE TABLE public.finance_payouts (
    id integer NOT NULL,
    invoice_id integer,
    counterparty_id integer NOT NULL,
    entity_id integer NOT NULL,
    amount numeric(15,2) NOT NULL,
    currency character varying(3) NOT NULL,
    wise_profile_id character varying(32),
    wise_quote_id character varying(64),
    wise_transfer_id character varying(64),
    idempotency_key character varying(80),
    state character varying(20) NOT NULL,
    requires_checker boolean NOT NULL,
    failure_reason text,
    is_dry_run boolean NOT NULL,
    requested_by character varying(120),
    requested_at timestamp without time zone,
    approved_by character varying(120),
    approved_at timestamp without time zone,
    settled_at timestamp without time zone,
    transaction_id integer,
    match_id integer,
    journal_entry_id integer,
    created_at timestamp without time zone NOT NULL,
    method character varying(20) DEFAULT 'system_wise'::character varying NOT NULL,
    external_reference character varying(120),
    payable_type character varying(16) DEFAULT 'invoice'::character varying NOT NULL,
    payable_id integer,
    channel_id integer,
    registration_id integer,
    amount_sgd numeric(15,2)
);


ALTER TABLE public.finance_payouts OWNER TO collectionsagent;

--
-- Name: finance_vendor_payouts_id_seq; Type: SEQUENCE; Schema: public; Owner: collectionsagent
--

CREATE SEQUENCE public.finance_vendor_payouts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.finance_vendor_payouts_id_seq OWNER TO collectionsagent;

--
-- Name: finance_vendor_payouts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: collectionsagent
--

ALTER SEQUENCE public.finance_vendor_payouts_id_seq OWNED BY public.finance_payouts.id;


--
-- Name: finance_coa_config id; Type: DEFAULT; Schema: public; Owner: collectionsagent
--

ALTER TABLE ONLY public.finance_coa_config ALTER COLUMN id SET DEFAULT nextval('public.finance_coa_config_id_seq'::regclass);


--
-- Name: finance_payouts id; Type: DEFAULT; Schema: public; Owner: collectionsagent
--

ALTER TABLE ONLY public.finance_payouts ALTER COLUMN id SET DEFAULT nextval('public.finance_vendor_payouts_id_seq'::regclass);


--
-- Name: finance_coa_config finance_coa_config_coa_code_key; Type: CONSTRAINT; Schema: public; Owner: collectionsagent
--

ALTER TABLE ONLY public.finance_coa_config
    ADD CONSTRAINT finance_coa_config_coa_code_key UNIQUE (coa_code);


--
-- Name: finance_coa_config finance_coa_config_pkey; Type: CONSTRAINT; Schema: public; Owner: collectionsagent
--

ALTER TABLE ONLY public.finance_coa_config
    ADD CONSTRAINT finance_coa_config_pkey PRIMARY KEY (id);


--
-- Name: finance_payouts finance_vendor_payouts_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: collectionsagent
--

ALTER TABLE ONLY public.finance_payouts
    ADD CONSTRAINT finance_vendor_payouts_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: finance_payouts finance_vendor_payouts_pkey; Type: CONSTRAINT; Schema: public; Owner: collectionsagent
--

ALTER TABLE ONLY public.finance_payouts
    ADD CONSTRAINT finance_vendor_payouts_pkey PRIMARY KEY (id);


--
-- Name: ix_finance_coa_config_coa_code; Type: INDEX; Schema: public; Owner: collectionsagent
--

CREATE INDEX ix_finance_coa_config_coa_code ON public.finance_coa_config USING btree (coa_code);


--
-- Name: ix_fvp_invoice; Type: INDEX; Schema: public; Owner: collectionsagent
--

CREATE INDEX ix_fvp_invoice ON public.finance_payouts USING btree (invoice_id);


--
-- Name: ix_fvp_method; Type: INDEX; Schema: public; Owner: collectionsagent
--

CREATE INDEX ix_fvp_method ON public.finance_payouts USING btree (method);


--
-- Name: ix_fvp_payable; Type: INDEX; Schema: public; Owner: collectionsagent
--

CREATE INDEX ix_fvp_payable ON public.finance_payouts USING btree (payable_type, payable_id);


--
-- Name: ix_fvp_state; Type: INDEX; Schema: public; Owner: collectionsagent
--

CREATE INDEX ix_fvp_state ON public.finance_payouts USING btree (state);


--
-- Name: ix_fvp_transfer; Type: INDEX; Schema: public; Owner: collectionsagent
--

CREATE INDEX ix_fvp_transfer ON public.finance_payouts USING btree (wise_transfer_id);


--
-- Name: finance_payouts finance_vendor_payouts_counterparty_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: collectionsagent
--

ALTER TABLE ONLY public.finance_payouts
    ADD CONSTRAINT finance_vendor_payouts_counterparty_id_fkey FOREIGN KEY (counterparty_id) REFERENCES public.finance_counterparties(id);


--
-- Name: finance_payouts finance_vendor_payouts_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: collectionsagent
--

ALTER TABLE ONLY public.finance_payouts
    ADD CONSTRAINT finance_vendor_payouts_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.finance_entities(id);


--
-- Name: finance_payouts finance_vendor_payouts_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: collectionsagent
--

ALTER TABLE ONLY public.finance_payouts
    ADD CONSTRAINT finance_vendor_payouts_invoice_id_fkey FOREIGN KEY (invoice_id) REFERENCES public.finance_invoices(id);


--
-- Name: finance_payouts finance_vendor_payouts_journal_entry_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: collectionsagent
--

ALTER TABLE ONLY public.finance_payouts
    ADD CONSTRAINT finance_vendor_payouts_journal_entry_id_fkey FOREIGN KEY (journal_entry_id) REFERENCES public.finance_journal_entries(id);


--
-- Name: finance_payouts finance_vendor_payouts_match_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: collectionsagent
--

ALTER TABLE ONLY public.finance_payouts
    ADD CONSTRAINT finance_vendor_payouts_match_id_fkey FOREIGN KEY (match_id) REFERENCES public.finance_invoice_payment_matches(id);


--
-- Name: finance_payouts finance_vendor_payouts_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: collectionsagent
--

ALTER TABLE ONLY public.finance_payouts
    ADD CONSTRAINT finance_vendor_payouts_transaction_id_fkey FOREIGN KEY (transaction_id) REFERENCES public.finance_transactions(id);


--
-- Name: finance_payouts fk_finance_payouts_channel; Type: FK CONSTRAINT; Schema: public; Owner: collectionsagent
--

ALTER TABLE ONLY public.finance_payouts
    ADD CONSTRAINT fk_finance_payouts_channel FOREIGN KEY (channel_id) REFERENCES public.payment_channel(id);


--
-- Name: finance_payouts fk_finance_payouts_registration; Type: FK CONSTRAINT; Schema: public; Owner: collectionsagent
--

ALTER TABLE ONLY public.finance_payouts
    ADD CONSTRAINT fk_finance_payouts_registration FOREIGN KEY (registration_id) REFERENCES public.payout_channel_registration(id);


--
-- PostgreSQL database dump complete
--

\unrestrict AYl8Mdm7p14ecaYT0RqdB6LrGmVJeiLKnW77cQgO1WlNa9kIMz8ufkp5i1Wcr9J

--
-- PostgreSQL database dump
--

\restrict DDmaRUICb57T4LGWRaEq4TeHTQrhaSdxMUhOrJz1vDcUgL5gYpMp8LMotI4ned6

-- Dumped from database version 17.9
-- Dumped by pg_dump version 17.10 (Homebrew)

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

--
-- Data for Name: finance_coa_config; Type: TABLE DATA; Schema: public; Owner: collectionsagent
--

COPY public.finance_coa_config (id, coa_code, approval_threshold_sgd, approver_1, approver_2, second_approver_above_sgd, auto_approve_ok, needs_trip_id, needs_intercom_id, other_required, notes, updated_by, updated_at) FROM stdin;
1	5033	\N	\N	\N	\N	f	t	t	\N	\N	gauravs@drivelah.sg	2026-08-09 06:10:26.616656+00
\.


--
-- Name: finance_coa_config_id_seq; Type: SEQUENCE SET; Schema: public; Owner: collectionsagent
--

SELECT pg_catalog.setval('public.finance_coa_config_id_seq', 1, true);


--
-- PostgreSQL database dump complete
--

\unrestrict DDmaRUICb57T4LGWRaEq4TeHTQrhaSdxMUhOrJz1vDcUgL5gYpMp8LMotI4ned6

