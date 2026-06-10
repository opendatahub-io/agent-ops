-- RedBank Kagenti Demo — Database Schema with Row-Level Security
--
-- Adapted from redbank-demo/postgres-db/init.sql.
-- Adds: user_accounts mapping, RLS policies, admin/user role distinction.

-- =============================================================================
-- Tables
-- =============================================================================

CREATE TABLE IF NOT EXISTS customers (
    customer_id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    phone VARCHAR(50),
    address TEXT,
    account_type VARCHAR(50),
    date_of_birth DATE,
    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS statements (
    statement_id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    statement_period_start DATE NOT NULL,
    statement_period_end DATE NOT NULL,
    balance DECIMAL(15, 2) NOT NULL,
    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id SERIAL PRIMARY KEY,
    statement_id INTEGER NOT NULL,
    transaction_date TIMESTAMP NOT NULL,
    amount DECIMAL(15, 2) NOT NULL,
    description TEXT,
    transaction_type VARCHAR(50) NOT NULL,
    merchant VARCHAR(255),
    FOREIGN KEY (statement_id) REFERENCES statements(statement_id) ON DELETE CASCADE
);

-- Maps Keycloak identities to customer records and roles.
-- Admin users (role='admin') have NULL customer_id — they see all rows.
-- Regular users (role='user') are bound to a specific customer_id via RLS.
CREATE TABLE IF NOT EXISTS user_accounts (
    email VARCHAR(255) PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(customer_id),
    role VARCHAR(20) NOT NULL DEFAULT 'user'
);

-- =============================================================================
-- Indexes
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email);
CREATE INDEX IF NOT EXISTS idx_statements_customer_id ON statements(customer_id);
CREATE INDEX IF NOT EXISTS idx_statements_period ON statements(statement_period_start, statement_period_end);
CREATE INDEX IF NOT EXISTS idx_transactions_statement_id ON transactions(statement_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(transaction_type);

-- =============================================================================
-- Seed Data — Customers
-- =============================================================================

INSERT INTO customers (name, email, phone, address, account_type, date_of_birth) VALUES
    ('Alice Johnson', 'alice.johnson@email.com', '555-0101', '123 Oak Street, Springfield, IL', 'CHECKING', '1990-03-15'),
    ('Bob Smith', 'bob.smith@email.com', '555-0102', '456 Maple Ave, Boston, MA', 'SAVINGS', '1985-07-22'),
    ('Carol Williams', 'carol.williams@email.com', '555-0103', '789 Pine Road, Seattle, WA', 'CHECKING', '1992-11-08'),
    ('David Brown', 'david.brown@email.com', '555-0104', '321 Elm Drive, Austin, TX', 'BUSINESS', '1980-05-30'),
    ('John Doe', 'john@redbank.demo', '555-0105', '555 Cedar Lane, Denver, CO', 'CHECKING', '1988-09-12')
ON CONFLICT (email) DO NOTHING;

-- =============================================================================
-- Seed Data — Statements
-- =============================================================================

INSERT INTO statements (customer_id, statement_period_start, statement_period_end, balance) VALUES
    (1, '2025-01-01', '2025-01-31', 5420.50),
    (1, '2025-02-01', '2025-02-28', 6780.25),
    (1, '2025-03-01', '2025-03-31', 3210.75),
    (2, '2025-01-01', '2025-01-31', 12500.00),
    (2, '2025-02-01', '2025-02-28', 13200.50),
    (3, '2025-01-01', '2025-01-31', 875.25),
    (3, '2025-02-01', '2025-02-28', 2100.00),
    (3, '2025-03-01', '2025-03-31', 3500.75),
    (3, '2025-04-01', '2025-04-30', 4200.00),
    (4, '2025-01-01', '2025-01-31', 25000.00),
    (4, '2025-02-01', '2025-02-28', 27500.00),
    (5, '2025-01-01', '2025-01-31', 8750.00),
    (5, '2025-02-01', '2025-02-28', 9200.30)
ON CONFLICT DO NOTHING;

-- =============================================================================
-- Seed Data — Transactions
-- =============================================================================

INSERT INTO transactions (statement_id, transaction_date, amount, description, transaction_type, merchant) VALUES
    (1, '2025-01-05 10:30:00', -45.99, 'Grocery shopping', 'DEBIT', 'Fresh Market'),
    (1, '2025-01-10 14:22:00', -15.50, 'Coffee', 'DEBIT', 'Starbucks'),
    (1, '2025-01-15 09:15:00', 3000.00, 'Salary deposit', 'CREDIT', 'Acme Corp'),
    (1, '2025-01-20 16:45:00', -120.00, 'Electric bill', 'DEBIT', 'Power Company'),
    (2, '2025-02-03 11:20:00', -89.99, 'Online purchase', 'DEBIT', 'Amazon'),
    (2, '2025-02-08 13:30:00', -35.00, 'Lunch', 'DEBIT', 'Cafe Delight'),
    (2, '2025-02-15 09:15:00', 3000.00, 'Salary deposit', 'CREDIT', 'Acme Corp'),
    (2, '2025-02-22 10:00:00', -450.00, 'Rent payment', 'DEBIT', 'Landlord Inc'),
    (4, '2025-01-07 08:45:00', 5000.00, 'Initial deposit', 'CREDIT', 'Transfer'),
    (4, '2025-01-10 12:30:00', -250.00, 'Monthly savings', 'DEBIT', 'Savings Transfer'),
    (4, '2025-01-25 10:15:00', -150.00, 'Insurance payment', 'DEBIT', 'Insurance Co'),
    (6, '2025-01-04 15:20:00', -25.00, 'Gas station', 'DEBIT', 'Shell Station'),
    (6, '2025-01-12 09:30:00', -80.00, 'Pharmacy', 'DEBIT', 'CVS Pharmacy'),
    (6, '2025-01-18 11:00:00', 2000.00, 'Freelance payment', 'CREDIT', 'Client ABC'),
    (6, '2025-01-25 14:45:00', -150.00, 'Internet bill', 'DEBIT', 'Comcast'),
    (10, '2025-01-02 09:00:00', -1500.00, 'Office supplies', 'DEBIT', 'Office Depot'),
    (10, '2025-01-05 13:30:00', -500.00, 'Client lunch', 'DEBIT', 'Restaurant XYZ'),
    (10, '2025-01-15 09:15:00', 30000.00, 'Business deposit', 'CREDIT', 'Client Payment'),
    (10, '2025-01-22 10:30:00', -2500.00, 'Vendor payment', 'DEBIT', 'Supplier Co'),
    (12, '2025-01-03 09:00:00', 4500.00, 'Salary deposit', 'CREDIT', 'TechCorp'),
    (12, '2025-01-08 12:15:00', -65.00, 'Grocery shopping', 'DEBIT', 'Whole Foods'),
    (12, '2025-01-14 17:30:00', -42.50, 'Gas station', 'DEBIT', 'BP Station'),
    (12, '2025-01-20 08:45:00', -1200.00, 'Rent payment', 'DEBIT', 'Denver Housing'),
    (12, '2025-01-28 14:00:00', -85.00, 'Phone bill', 'DEBIT', 'Verizon'),
    (13, '2025-02-03 09:00:00', 4500.00, 'Salary deposit', 'CREDIT', 'TechCorp'),
    (13, '2025-02-10 11:30:00', -120.00, 'Electric bill', 'DEBIT', 'Xcel Energy'),
    (13, '2025-02-18 16:00:00', -55.00, 'Dinner', 'DEBIT', 'Chipotle')
ON CONFLICT DO NOTHING;

-- =============================================================================
-- Seed Data — User-Account Mapping (Keycloak identity -> customer)
-- =============================================================================

INSERT INTO user_accounts (email, customer_id, role) VALUES
    ('john@redbank.demo', 5, 'user'),
    ('jane@redbank.demo', NULL, 'admin')
ON CONFLICT (email) DO NOTHING;

-- =============================================================================
-- Application Role
-- =============================================================================
--
-- The community pgvector image creates $POSTGRESQL_USER as a superuser.
-- Superusers always bypass RLS, so we create a non-superuser app role
-- for application connections (MCP server, pipeline, notebook).
-- =============================================================================

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app') THEN
        CREATE ROLE app WITH LOGIN PASSWORD 'app';
    END IF;
END $$;

-- =============================================================================
-- Ownership & Grants
-- =============================================================================

ALTER TABLE customers OWNER TO "$POSTGRESQL_USER";
ALTER TABLE statements OWNER TO "$POSTGRESQL_USER";
ALTER TABLE transactions OWNER TO "$POSTGRESQL_USER";
ALTER TABLE user_accounts OWNER TO "$POSTGRESQL_USER";

GRANT ALL PRIVILEGES ON TABLE customers TO app;
GRANT ALL PRIVILEGES ON TABLE statements TO app;
GRANT ALL PRIVILEGES ON TABLE transactions TO app;
GRANT ALL PRIVILEGES ON TABLE user_accounts TO app;

GRANT ALL PRIVILEGES ON SEQUENCE customers_customer_id_seq TO app;
GRANT ALL PRIVILEGES ON SEQUENCE statements_statement_id_seq TO app;
GRANT ALL PRIVILEGES ON SEQUENCE transactions_transaction_id_seq TO app;

-- PGVector (langchain-postgres) creates internal metadata tables on init.
GRANT CREATE ON SCHEMA public TO app;

-- =============================================================================
-- Row-Level Security
-- =============================================================================
--
-- RLS is enforced at the application level: the MCP server sets session
-- variables (app.current_role, app.current_user_email) via SET LOCAL before
-- each query.  FORCE ROW LEVEL SECURITY makes policies apply to the table
-- owner role, but PostgreSQL always bypasses RLS for superusers and roles
-- with BYPASSRLS.  The MCP server therefore runs as the unprivileged 'app'
-- role (SET LOCAL ROLE app) so that policies are evaluated correctly.
-- =============================================================================

ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers FORCE ROW LEVEL SECURITY;

ALTER TABLE statements ENABLE ROW LEVEL SECURITY;
ALTER TABLE statements FORCE ROW LEVEL SECURITY;

ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE transactions FORCE ROW LEVEL SECURITY;

-- Admin policies: full access when app.current_role = 'admin'

CREATE POLICY admin_all_customers ON customers
    FOR ALL
    USING (current_setting('app.current_role', true) = 'admin')
    WITH CHECK (current_setting('app.current_role', true) = 'admin');

CREATE POLICY admin_all_statements ON statements
    FOR ALL
    USING (current_setting('app.current_role', true) = 'admin')
    WITH CHECK (current_setting('app.current_role', true) = 'admin');

CREATE POLICY admin_all_transactions ON transactions
    FOR ALL
    USING (current_setting('app.current_role', true) = 'admin')
    WITH CHECK (current_setting('app.current_role', true) = 'admin');

-- User policies: restrict to the customer_id mapped to the current user's email.
-- The subquery looks up the customer_id from user_accounts.

CREATE POLICY user_own_customers ON customers
    FOR SELECT
    USING (
        current_setting('app.current_role', true) = 'user'
        AND customer_id = (
            SELECT ua.customer_id FROM user_accounts ua
            WHERE ua.email = current_setting('app.current_user_email', true)
        )
    );

CREATE POLICY user_own_statements ON statements
    FOR SELECT
    USING (
        current_setting('app.current_role', true) = 'user'
        AND customer_id = (
            SELECT ua.customer_id FROM user_accounts ua
            WHERE ua.email = current_setting('app.current_user_email', true)
        )
    );

CREATE POLICY user_own_transactions ON transactions
    FOR SELECT
    USING (
        current_setting('app.current_role', true) = 'user'
        AND statement_id IN (
            SELECT s.statement_id FROM statements s
            WHERE s.customer_id = (
                SELECT ua.customer_id FROM user_accounts ua
                WHERE ua.email = current_setting('app.current_user_email', true)
            )
        )
    );

-- =============================================================================
-- PGVector — Embeddings Table & Session-Variable RLS
-- =============================================================================
--
-- This section supports the LangChain + PGVector RAG pipeline. It uses the
-- same session-variable RLS pattern as the tables above: the caller sets
-- app.current_role (from a Keycloak JWT) before each query, and PostgreSQL
-- policies filter rows accordingly.
--
-- Admin (app.current_role = 'admin'): full read/write on all collections.
-- User  (app.current_role = 'user'):  read-only on the 'user' collection.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS embeddings (
    langchain_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    collection VARCHAR(64) NOT NULL,
    content TEXT NOT NULL,
    embedding vector(768) NOT NULL,
    langchain_metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_embeddings_collection ON embeddings(collection);

ALTER TABLE embeddings OWNER TO "$POSTGRESQL_USER";

GRANT ALL PRIVILEGES ON TABLE embeddings TO app;

ALTER TABLE embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE embeddings FORCE ROW LEVEL SECURITY;

-- Admin: full access to all collections
CREATE POLICY admin_all_embeddings ON embeddings
    FOR ALL
    USING (current_setting('app.current_role', true) = 'admin')
    WITH CHECK (current_setting('app.current_role', true) = 'admin');

-- User: read-only, restricted to 'user' collection
CREATE POLICY user_select_embeddings ON embeddings
    FOR SELECT
    USING (
        current_setting('app.current_role', true) = 'user'
        AND collection = 'user'
    );
