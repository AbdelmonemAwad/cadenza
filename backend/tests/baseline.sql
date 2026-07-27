-- Baseline schema: what a Cadenza database looked like when schema
-- versioning was introduced (schema v1).
--
-- Frozen on purpose. tests/test_migrations.py builds a database from this
-- file, runs the migrations over it, and compares the result with what the
-- current models produce. Editing this file to match a model change defeats
-- the guard -- write a migration instead.

CREATE TABLE audit_log (
	id INTEGER NOT NULL, 
	ts DATETIME NOT NULL, 
	action VARCHAR(64) NOT NULL, 
	level VARCHAR(16) NOT NULL, 
	track_id INTEGER, 
	job_id INTEGER, 
	src_path VARCHAR(1024), 
	dst_path VARCHAR(1024), 
	detail JSON, 
	reversible BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE duplicate_groups (
	id INTEGER NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	signature VARCHAR(128) NOT NULL, 
	confidence FLOAT NOT NULL, 
	member_count INTEGER NOT NULL, 
	reclaimable_bytes INTEGER NOT NULL, 
	resolved BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE duplicate_members (
	id INTEGER NOT NULL, 
	group_id INTEGER NOT NULL, 
	track_id INTEGER NOT NULL, 
	score FLOAT NOT NULL, 
	score_breakdown JSON, 
	proposed_action VARCHAR(16) NOT NULL, 
	applied BOOLEAN NOT NULL, 
	reason TEXT, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_group_track UNIQUE (group_id, track_id), 
	FOREIGN KEY(group_id) REFERENCES duplicate_groups (id) ON DELETE CASCADE, 
	FOREIGN KEY(track_id) REFERENCES tracks (id) ON DELETE CASCADE
);
CREATE TABLE jobs (
	id INTEGER NOT NULL, 
	kind VARCHAR(48) NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	params JSON, 
	dry_run BOOLEAN NOT NULL, 
	total INTEGER NOT NULL, 
	processed INTEGER NOT NULL, 
	succeeded INTEGER NOT NULL, 
	failed INTEGER NOT NULL, 
	message TEXT, 
	result JSON, 
	created_at DATETIME NOT NULL, 
	started_at DATETIME, 
	finished_at DATETIME, 
	PRIMARY KEY (id)
);
CREATE TABLE playlists (
	id INTEGER NOT NULL, 
	name VARCHAR(256) NOT NULL, 
	source VARCHAR(32) NOT NULL, 
	external_id VARCHAR(128), 
	track_ids JSON, 
	unmatched JSON, 
	synced_at DATETIME, 
	PRIMARY KEY (id)
);
CREATE TABLE provider_cache (
	id INTEGER NOT NULL, 
	provider VARCHAR(32) NOT NULL, 
	cache_key VARCHAR(256) NOT NULL, 
	payload JSON, 
	fetched_at DATETIME NOT NULL, 
	expires_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_provider_key UNIQUE (provider, cache_key)
);
CREATE TABLE quarantine (
	id INTEGER NOT NULL, 
	original_path VARCHAR(1024) NOT NULL, 
	quarantine_path VARCHAR(1024) NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	sha256 VARCHAR(64), 
	reason VARCHAR(256) NOT NULL, 
	group_id INTEGER, 
	moved_at DATETIME NOT NULL, 
	purge_after DATETIME, 
	restored BOOLEAN NOT NULL, 
	restored_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (quarantine_path)
);
CREATE TABLE scheduled_tasks (
	id INTEGER NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	job_kind VARCHAR(48) NOT NULL, 
	cron VARCHAR(64) NOT NULL, 
	params JSON, 
	enabled BOOLEAN NOT NULL, 
	last_run DATETIME, 
	next_run DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);
CREATE TABLE tracks (
	id INTEGER NOT NULL, 
	path VARCHAR(1024) NOT NULL, 
	filename VARCHAR(512) NOT NULL, 
	ext VARCHAR(16) NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	mtime FLOAT NOT NULL, 
	inode INTEGER, 
	codec VARCHAR(32), 
	lossless BOOLEAN NOT NULL, 
	bitrate INTEGER, 
	sample_rate INTEGER, 
	bit_depth INTEGER, 
	channels INTEGER, 
	duration FLOAT, 
	sha256 VARCHAR(64), 
	audio_md5 VARCHAR(32), 
	fingerprint TEXT, 
	acoustid VARCHAR(64), 
	title VARCHAR(512), 
	artist VARCHAR(512), 
	albumartist VARCHAR(512), 
	album VARCHAR(512), 
	year INTEGER, 
	track_no INTEGER, 
	disc_no INTEGER, 
	total_tracks INTEGER, 
	genre VARCHAR(256), 
	isrc VARCHAR(32), 
	mb_recording_id VARCHAR(64), 
	mb_release_id VARCHAR(64), 
	apple_id VARCHAR(64), 
	has_artwork BOOLEAN NOT NULL, 
	artwork_px INTEGER, 
	has_lyrics BOOLEAN NOT NULL, 
	has_synced_lyrics BOOLEAN NOT NULL, 
	tag_completeness FLOAT NOT NULL, 
	quality_score FLOAT NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	error TEXT, 
	first_seen DATETIME NOT NULL, 
	last_scan DATETIME NOT NULL, 
	raw_tags JSON, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_audit_log_action ON audit_log (action);
CREATE INDEX ix_audit_log_job_id ON audit_log (job_id);
CREATE INDEX ix_audit_log_level ON audit_log (level);
CREATE INDEX ix_audit_log_track_id ON audit_log (track_id);
CREATE INDEX ix_audit_log_ts ON audit_log (ts);
CREATE INDEX ix_duplicate_groups_kind ON duplicate_groups (kind);
CREATE INDEX ix_duplicate_groups_resolved ON duplicate_groups (resolved);
CREATE INDEX ix_duplicate_groups_signature ON duplicate_groups (signature);
CREATE INDEX ix_duplicate_members_group_id ON duplicate_members (group_id);
CREATE INDEX ix_duplicate_members_track_id ON duplicate_members (track_id);
CREATE INDEX ix_jobs_created_at ON jobs (created_at);
CREATE INDEX ix_jobs_kind ON jobs (kind);
CREATE INDEX ix_jobs_state ON jobs (state);
CREATE INDEX ix_playlists_external_id ON playlists (external_id);
CREATE INDEX ix_playlists_name ON playlists (name);
CREATE INDEX ix_provider_cache_cache_key ON provider_cache (cache_key);
CREATE INDEX ix_provider_cache_expires_at ON provider_cache (expires_at);
CREATE INDEX ix_provider_cache_provider ON provider_cache (provider);
CREATE INDEX ix_quarantine_group_id ON quarantine (group_id);
CREATE INDEX ix_quarantine_moved_at ON quarantine (moved_at);
CREATE INDEX ix_quarantine_original_path ON quarantine (original_path);
CREATE INDEX ix_quarantine_purge_after ON quarantine (purge_after);
CREATE INDEX ix_quarantine_restored ON quarantine (restored);
CREATE INDEX ix_scheduled_tasks_enabled ON scheduled_tasks (enabled);
CREATE INDEX ix_tracks_acoustid ON tracks (acoustid);
CREATE INDEX ix_tracks_album ON tracks (album);
CREATE INDEX ix_tracks_album_group ON tracks (albumartist, album);
CREATE INDEX ix_tracks_albumartist ON tracks (albumartist);
CREATE INDEX ix_tracks_apple_id ON tracks (apple_id);
CREATE INDEX ix_tracks_artist ON tracks (artist);
CREATE INDEX ix_tracks_audio_md5 ON tracks (audio_md5);
CREATE INDEX ix_tracks_bitrate ON tracks (bitrate);
CREATE INDEX ix_tracks_codec ON tracks (codec);
CREATE INDEX ix_tracks_dupkey ON tracks (duration, title);
CREATE INDEX ix_tracks_duration ON tracks (duration);
CREATE INDEX ix_tracks_ext ON tracks (ext);
CREATE INDEX ix_tracks_isrc ON tracks (isrc);
CREATE INDEX ix_tracks_lossless ON tracks (lossless);
CREATE INDEX ix_tracks_mb_recording_id ON tracks (mb_recording_id);
CREATE INDEX ix_tracks_mb_release_id ON tracks (mb_release_id);
CREATE UNIQUE INDEX ix_tracks_path ON tracks (path);
CREATE INDEX ix_tracks_quality_score ON tracks (quality_score);
CREATE INDEX ix_tracks_sha256 ON tracks (sha256);
CREATE INDEX ix_tracks_status ON tracks (status);
CREATE INDEX ix_tracks_year ON tracks (year);
