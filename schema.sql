-- IS ACCREDISYS database schema (SRS FR-01..FR-12, BR-1..BR-10)
PRAGMA foreign_keys = ON;

-- FR-01, FR-02, FR-12, BR-1, BR-2, BR-6, BR-10
CREATE TABLE IF NOT EXISTS users (
  id              INTEGER PRIMARY KEY,
  email           TEXT NOT NULL UNIQUE,
  full_name       TEXT NOT NULL,
  password_hash   TEXT NOT NULL,
  role            TEXT NOT NULL CHECK (role IN ('dept_chair','faculty','external_evaluator')),
  totp_secret     TEXT NOT NULL,                 -- BR-6: authenticator (TOTP) is mandatory
  is_active       INTEGER NOT NULL DEFAULT 1,    -- BR-2: deactivation instead of deletion
  expires_at      TEXT,                          -- BR-10: temporary evaluator accounts
  failed_logins   INTEGER NOT NULL DEFAULT 0,
  locked_until    TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  deactivated_at  TEXT
);

-- FR-03 / FR-05: document metadata used for search and filtering
CREATE TABLE IF NOT EXISTS categories (
  id        INTEGER PRIMARY KEY,
  name      TEXT NOT NULL,
  parent_id INTEGER REFERENCES categories(id),   -- main categories -> subcategories (interview 9/11)
  UNIQUE(name, parent_id)
);

CREATE TABLE IF NOT EXISTS courses (
  id    INTEGER PRIMARY KEY,
  code  TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  id               INTEGER PRIMARY KEY,
  title            TEXT NOT NULL,
  description      TEXT NOT NULL DEFAULT '',
  category_id      INTEGER NOT NULL REFERENCES categories(id),
  course_id        INTEGER REFERENCES courses(id),
  school_year      TEXT NOT NULL,                -- e.g. 2026-2027
  semester         TEXT NOT NULL CHECK (semester IN ('1st','2nd','Summer')),
  document_type    TEXT NOT NULL,                -- syllabus, exam, credential, ...
  owner_id         INTEGER NOT NULL REFERENCES users(id),   -- BR-9 data owner
  current_version_id INTEGER,                    -- latest *approved* version (BR-3)
  created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- FR-10, BR-3, BR-4, BR-5: versions are append-only
CREATE TABLE IF NOT EXISTS document_versions (
  id             INTEGER PRIMARY KEY,
  document_id    INTEGER NOT NULL REFERENCES documents(id),
  version_no     INTEGER NOT NULL,
  stored_path    TEXT NOT NULL,
  original_name  TEXT NOT NULL,
  sha256         TEXT NOT NULL,
  size_bytes     INTEGER NOT NULL,
  status         TEXT NOT NULL DEFAULT 'Pending Review'
                 CHECK (status IN ('Pending Review','Current Approved Version','Requires Revision','Superseded')),
  remarks        TEXT,
  uploaded_by    INTEGER NOT NULL REFERENCES users(id),
  uploaded_at    TEXT NOT NULL DEFAULT (datetime('now')),
  reviewed_by    INTEGER REFERENCES users(id),
  reviewed_at    TEXT,
  UNIQUE(document_id, version_no)
);

-- BR-5: no UPDATE of file fields and no DELETE on versions
CREATE TRIGGER IF NOT EXISTS trg_versions_no_delete
BEFORE DELETE ON document_versions
BEGIN SELECT RAISE(ABORT, 'BR-5: document versions cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS trg_versions_immutable_file
BEFORE UPDATE OF stored_path, sha256, version_no, document_id, original_name, size_bytes ON document_versions
BEGIN SELECT RAISE(ABORT, 'BR-5: stored version content is immutable'); END;

-- FR-07: signatories per document
CREATE TABLE IF NOT EXISTS signatories (
  id          INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id),
  version_id  INTEGER NOT NULL REFERENCES document_versions(id),
  signer_id   INTEGER NOT NULL REFERENCES users(id),
  signed_at   TEXT,
  UNIQUE(version_id, signer_id)
);

-- FR-06: accreditation requirement tracking
CREATE TABLE IF NOT EXISTS accreditation_bodies (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE                      -- e.g. PAASCU, CHED (names from interview 10/9)
);

CREATE TABLE IF NOT EXISTS requirements (
  id          INTEGER PRIMARY KEY,
  body_id     INTEGER NOT NULL REFERENCES accreditation_bodies(id),
  code        TEXT NOT NULL,
  description TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'Missing Evidence'
              CHECK (status IN ('Completed','In Progress','Missing Evidence')),
  UNIQUE(body_id, code)
);

CREATE TABLE IF NOT EXISTS requirement_evidence (
  requirement_id INTEGER NOT NULL REFERENCES requirements(id),
  document_id    INTEGER NOT NULL REFERENCES documents(id),
  linked_by      INTEGER NOT NULL REFERENCES users(id),
  linked_at      TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (requirement_id, document_id)
);

-- FR-08, BR-7: faculty profile data (privacy-sensitive)
CREATE TABLE IF NOT EXISTS faculty_profiles (
  id              INTEGER PRIMARY KEY,
  user_id         INTEGER UNIQUE REFERENCES users(id),
  employment_type TEXT NOT NULL DEFAULT 'Regular' CHECK (employment_type IN ('Regular','Part-time')),
  highest_degree  TEXT,                          -- doctoral / masteral / pursuing (interview 10/8)
  specialization  TEXT
);

CREATE TABLE IF NOT EXISTS faculty_records (
  id          INTEGER PRIMARY KEY,
  faculty_id  INTEGER NOT NULL REFERENCES faculty_profiles(id),
  kind        TEXT NOT NULL CHECK (kind IN ('education','credential','award','research','activity')),
  title       TEXT NOT NULL,
  detail      TEXT,
  year        INTEGER,
  scope       TEXT NOT NULL DEFAULT 'international' CHECK (scope IN ('local','international')),
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Evaluator access requests (BR-10)
CREATE TABLE IF NOT EXISTS access_requests (
  id            INTEGER PRIMARY KEY,
  evaluator_id  INTEGER NOT NULL REFERENCES users(id),
  document_id   INTEGER NOT NULL REFERENCES documents(id),
  status        TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending','Approved','Denied')),
  decided_by    INTEGER REFERENCES users(id),
  decided_at    TEXT,
  UNIQUE(evaluator_id, document_id)
);

-- Update/correction requests from the Chair to faculty (use case: Request updates or corrections)
CREATE TABLE IF NOT EXISTS revision_requests (
  id           INTEGER PRIMARY KEY,
  document_id  INTEGER NOT NULL REFERENCES documents(id),
  requested_by INTEGER NOT NULL REFERENCES users(id),
  assigned_to  INTEGER NOT NULL REFERENCES users(id),
  message      TEXT NOT NULL,
  resolved     INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Department dashboard statistics (interview 9/11: enrollees, graduates per year)
CREATE TABLE IF NOT EXISTS department_stats (
  school_year TEXT NOT NULL,
  metric      TEXT NOT NULL CHECK (metric IN ('enrollees','graduates')),
  year_level  INTEGER NOT NULL DEFAULT 0,
  value       INTEGER NOT NULL,
  PRIMARY KEY (school_year, metric, year_level)
);

-- Accountability for BR-1/BR-3/BR-5 etc.
CREATE TABLE IF NOT EXISTS audit_log (
  id         INTEGER PRIMARY KEY,
  user_id    INTEGER REFERENCES users(id),
  action     TEXT NOT NULL,
  entity     TEXT,
  entity_id  INTEGER,
  detail     TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS backups (
  id         INTEGER PRIMARY KEY,
  path       TEXT NOT NULL,
  kind       TEXT NOT NULL CHECK (kind IN ('scheduled','manual')),
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_docs_filter ON documents(category_id, course_id, school_year, semester, document_type);
CREATE INDEX IF NOT EXISTS idx_docs_title ON documents(title);
CREATE INDEX IF NOT EXISTS idx_versions_doc ON document_versions(document_id, version_no);
