"""library.db schema v1: the shape shipped in v0.1.0 and v0.1.1. Frozen; never edit.

Later changes are steps in steps.py.
"""

V1_TABLES = """CREATE TABLE IF NOT EXISTS "user" (
	id VARCHAR(36) NOT NULL,
	provider VARCHAR(32) NOT NULL,
	provider_subject VARCHAR(255) NOT NULL,
	display_name VARCHAR(255) NOT NULL,
	role VARCHAR(32) NOT NULL,
	rev INTEGER NOT NULL,
	updated_at DATETIME NOT NULL,
	deleted_at DATETIME,
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS source (
	id VARCHAR(36) NOT NULL,
	owner_user_id VARCHAR(36),
	backend VARCHAR(32) NOT NULL,
	root_uri TEXT NOT NULL,
	kind VARCHAR(32) NOT NULL,
	last_scan_at DATETIME,
	error TEXT,
	rev INTEGER NOT NULL,
	updated_at DATETIME NOT NULL,
	deleted_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES "user" (id)
);

CREATE TABLE IF NOT EXISTS local_credential (
	user_id VARCHAR(36) NOT NULL,
	password_hash TEXT NOT NULL,
	PRIMARY KEY (user_id),
	FOREIGN KEY(user_id) REFERENCES "user" (id)
);

CREATE TABLE IF NOT EXISTS job (
	id VARCHAR(36) NOT NULL,
	kind VARCHAR(64) NOT NULL,
	status VARCHAR(32) NOT NULL,
	created_at DATETIME NOT NULL,
	started_at DATETIME,
	finished_at DATETIME,
	user_id VARCHAR(36),
	input_json TEXT,
	result_json TEXT,
	error TEXT,
	processed INTEGER NOT NULL,
	total INTEGER NOT NULL,
	current TEXT,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES "user" (id)
);

CREATE TABLE IF NOT EXISTS source_grant (
	id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	source_id VARCHAR(36) NOT NULL,
	perm VARCHAR(16) NOT NULL,
	rev INTEGER NOT NULL,
	updated_at DATETIME NOT NULL,
	deleted_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_grant_user_source UNIQUE (user_id, source_id),
	FOREIGN KEY(user_id) REFERENCES "user" (id),
	FOREIGN KEY(source_id) REFERENCES source (id)
);

CREATE TABLE IF NOT EXISTS job_event (
	id VARCHAR(36) NOT NULL,
	job_id VARCHAR(36) NOT NULL,
	at DATETIME NOT NULL,
	level VARCHAR(16) NOT NULL,
	message TEXT NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(job_id) REFERENCES job (id)
);

CREATE TABLE IF NOT EXISTS asset (
	id VARCHAR(36) NOT NULL,
	source_id VARCHAR(36) NOT NULL,
	relative_path TEXT NOT NULL,
	size INTEGER NOT NULL,
	mtime FLOAT NOT NULL,
	mime VARCHAR(127),
	width INTEGER,
	height INTEGER,
	taken_at DATETIME,
	duration FLOAT,
	extra_json TEXT,
	content_hash VARCHAR(64),
	thumb_rev INTEGER NOT NULL,
	rev INTEGER NOT NULL,
	updated_at DATETIME NOT NULL,
	deleted_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_asset_source_path UNIQUE (source_id, relative_path),
	FOREIGN KEY(source_id) REFERENCES source (id)
);

CREATE TABLE IF NOT EXISTS album (
	id VARCHAR(36) NOT NULL,
	source_id VARCHAR(36),
	name VARCHAR(255) NOT NULL,
	cover_asset_id VARCHAR(36),
	sort INTEGER NOT NULL,
	rev INTEGER NOT NULL,
	updated_at DATETIME NOT NULL,
	deleted_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_album_source_name UNIQUE (source_id, name),
	FOREIGN KEY(source_id) REFERENCES source (id),
	FOREIGN KEY(cover_asset_id) REFERENCES asset (id)
);

CREATE TABLE IF NOT EXISTS album_asset (
	id VARCHAR(36) NOT NULL,
	album_id VARCHAR(36) NOT NULL,
	asset_id VARCHAR(36) NOT NULL,
	position INTEGER NOT NULL,
	rev INTEGER NOT NULL,
	updated_at DATETIME NOT NULL,
	deleted_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_album_asset UNIQUE (album_id, asset_id),
	FOREIGN KEY(album_id) REFERENCES album (id),
	FOREIGN KEY(asset_id) REFERENCES asset (id)
);
"""
