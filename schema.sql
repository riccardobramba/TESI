CREATE TABLE data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT NOT NULL,
        timestamp REAL NOT NULL,
        data BLOB NOT NULL
    );
CREATE TABLE sqlite_sequence(name,seq);
CREATE TABLE node (
        eui TEXT PRIMARY KEY,
        created_at REAL,
        label TEXT,
        version TEXT,
        comment TEXT
    );
CREATE TABLE sensor_reading (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_id INTEGER NOT NULL,
        eui TEXT NOT NULL,
        timestamp REAL NOT NULL,
        sensor_name TEXT NOT NULL,
        sensor_index INTEGER NOT NULL,
        value REAL NOT NULL,
        FOREIGN KEY (data_id) REFERENCES data(id),
        FOREIGN KEY (eui) REFERENCES node(eui)
    );
CREATE TABLE event (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        scope TEXT NOT NULL,
        eui TEXT,
        event_type TEXT NOT NULL,
        severity TEXT,
        message TEXT,
        data_id INTEGER,
        FOREIGN KEY (data_id) REFERENCES data(id),
        FOREIGN KEY (eui) REFERENCES node(eui)
    );
CREATE TABLE node_state_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        eui TEXT NOT NULL,
        state_name TEXT NOT NULL,
        value_text TEXT,
        value_num REAL,
        data_id INTEGER,
        FOREIGN KEY (data_id) REFERENCES data(id),
        FOREIGN KEY (eui) REFERENCES node(eui)
    );
CREATE TABLE node_state (
        eui TEXT NOT NULL,
        state_name TEXT NOT NULL,
        timestamp REAL NOT NULL,
        value_text TEXT,
        value_num REAL,
        data_id INTEGER,
        PRIMARY KEY (eui, state_name),
        FOREIGN KEY (data_id) REFERENCES data(id),
        FOREIGN KEY (eui) REFERENCES node(eui)
    );
CREATE TABLE node_parameter_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        eui TEXT NOT NULL,
        parameter_name TEXT NOT NULL,
        value_text TEXT,
        value_num REAL,
        data_id INTEGER,
        FOREIGN KEY (data_id) REFERENCES data(id),
        FOREIGN KEY (eui) REFERENCES node(eui)
    );
CREATE TABLE node_parameter (
        eui TEXT NOT NULL,
        parameter_name TEXT NOT NULL,
        timestamp REAL NOT NULL,
        value_text TEXT,
        value_num REAL,
        data_id INTEGER,
        PRIMARY KEY (eui, parameter_name),
        FOREIGN KEY (data_id) REFERENCES data(id),
        FOREIGN KEY (eui) REFERENCES node(eui)
    );
CREATE TABLE link_diagnostic (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        src_eui TEXT NOT NULL,
        dst_eui TEXT NOT NULL,
        metric_name TEXT NOT NULL,
        value_text TEXT,
        value_num REAL,
        data_id INTEGER,
        FOREIGN KEY (data_id) REFERENCES data(id),
        FOREIGN KEY (src_eui) REFERENCES node(eui),
        FOREIGN KEY (dst_eui) REFERENCES node(eui)
    );
CREATE TABLE network_diagnostic (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        metric_name TEXT NOT NULL,
        value_text TEXT,
        value_num REAL,
        data_id INTEGER,
        FOREIGN KEY (data_id) REFERENCES data(id)
    );
CREATE TRIGGER trg_node_state_log_to_current
    AFTER INSERT ON node_state_log
    BEGIN
        INSERT INTO node_state (eui, state_name, timestamp, value_text, value_num, data_id)
        VALUES (NEW.eui, NEW.state_name, NEW.timestamp, NEW.value_text, NEW.value_num, NEW.data_id)
        ON CONFLICT(eui, state_name) DO UPDATE SET
            timestamp = excluded.timestamp,
            value_text = excluded.value_text,
            value_num = excluded.value_num,
            data_id = excluded.data_id
        WHERE excluded.timestamp >= node_state.timestamp;
    END;
CREATE TRIGGER trg_node_parameter_log_to_current
    AFTER INSERT ON node_parameter_log
    BEGIN
        INSERT INTO node_parameter (eui, parameter_name, timestamp, value_text, value_num, data_id)
        VALUES (NEW.eui, NEW.parameter_name, NEW.timestamp, NEW.value_text, NEW.value_num, NEW.data_id)
        ON CONFLICT(eui, parameter_name) DO UPDATE SET
            timestamp = excluded.timestamp,
            value_text = excluded.value_text,
            value_num = excluded.value_num,
            data_id = excluded.data_id
        WHERE excluded.timestamp >= node_parameter.timestamp;
    END;
CREATE VIEW unified_log AS
    SELECT
        'sensor_reading' AS record_type,
        timestamp,
        eui AS subject_eui,
        NULL AS object_eui,
        sensor_name AS name,
        CAST(value AS TEXT) AS value,
        data_id
    FROM sensor_reading
    UNION ALL
    SELECT
        'event',
        timestamp,
        eui,
        NULL,
        event_type,
        COALESCE(message, severity),
        data_id
    FROM event
    UNION ALL
    SELECT
        'node_state',
        timestamp,
        eui,
        NULL,
        state_name,
        COALESCE(value_text, CAST(value_num AS TEXT)),
        data_id
    FROM node_state_log
    UNION ALL
    SELECT
        'node_parameter',
        timestamp,
        eui,
        NULL,
        parameter_name,
        COALESCE(value_text, CAST(value_num AS TEXT)),
        data_id
    FROM node_parameter_log
    UNION ALL
    SELECT
        'link_diagnostic',
        timestamp,
        src_eui,
        dst_eui,
        metric_name,
        COALESCE(value_text, CAST(value_num AS TEXT)),
        data_id
    FROM link_diagnostic
    UNION ALL
    SELECT
        'network_diagnostic',
        timestamp,
        NULL,
        NULL,
        metric_name,
        COALESCE(value_text, CAST(value_num AS TEXT)),
        data_id
    FROM network_diagnostic
/* unified_log(record_type,timestamp,subject_eui,object_eui,name,value,data_id) */;
CREATE INDEX idx_data_timestamp ON data(timestamp);
CREATE INDEX idx_sensor_timestamp ON sensor_reading(timestamp);
CREATE INDEX idx_sensor_eui_timestamp ON sensor_reading(eui, timestamp);
CREATE INDEX idx_event_timestamp ON event(timestamp);
CREATE INDEX idx_event_eui_timestamp ON event(eui, timestamp);
CREATE INDEX idx_node_state_log_eui_timestamp ON node_state_log(eui, timestamp);
CREATE INDEX idx_node_state_current_ts ON node_state(timestamp);
CREATE INDEX idx_node_parameter_log_eui_timestamp ON node_parameter_log(eui, timestamp);
CREATE INDEX idx_node_parameter_current_ts ON node_parameter(timestamp);
CREATE INDEX idx_link_diag_src_dst_ts ON link_diagnostic(src_eui, dst_eui, timestamp);
CREATE INDEX idx_network_diag_metric_ts ON network_diagnostic(metric_name, timestamp);
