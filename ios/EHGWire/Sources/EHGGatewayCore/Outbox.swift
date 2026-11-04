// PURPOSE: Durable store-and-forward queue for the phone's MQTT messages. A port of gateway/outbox.py: every message is written to disk first and deleted only when the broker's PUBACK arrives, so a signal loss, a broker outage
//   or the app being killed loses nothing (the board's own history covers only about two minutes).
// INPUT: add(nodeId, seq, tInject, topic, payload) per packet (payload = the JSON without t_mqtt_pub); setConnected(true/false) from the MQTT client; acked(token) from its PUBACK callback.
// OUTPUT: transport.send(topic, payload) -> token calls, oldest first, at most maxInflight unacknowledged. The payload sent is the stored JSON plus "replayed": true (only when it did not go out the moment it was added) and a fresh
//   "t_mqtt_pub": now. Over the size cap the OLDEST rows are deleted and the loss is reported through `log`.
// HOW: SQLite (WAL) in a file whose protection class is "complete until first user authentication" on iOS, so a locked phone can still append to it. One recursive lock guards everything (BLE callbacks and the MQTT queue call in from
//   different threads). No file lock: an iOS app is one process. A duplicate after a lost PUBACK is harmless (the cloud keys on node, seq, t_inject).
// STATUS: tested with `swift test` against a recording transport. Never run on a phone.
// ROLE IN THESIS: Gateway resilience beyond the board's 256-packet history (H2).
// BEFORE -> AFTER: Before: the Python outbox only. After: the same behaviour on the phone.
import Foundation
import SQLite3

public protocol OutboxTransport: AnyObject {
    /// Hand one message to the MQTT client; the token comes back in `acked`. nil = the client would not take it.
    func send(topic: String, payload: String) -> Int?
}

public final class Outbox {
    public private(set) var dropped = 0, errors = 0, staleAcks = 0
    private var db: OpaquePointer?
    private let transport: OutboxTransport
    private let maxBytes: Int, maxInflight: Int, clock: () -> Double, log: (String) -> Void
    private let lock = NSRecursiveLock()
    private var connected = false
    private var inflight: [Int: Int64] = [:]       // token -> row id
    private var inflightIds = Set<Int64>()

    public init(path: String, transport: OutboxTransport, maxBytes: Int = 512 << 20, maxInflight: Int = 100, clock: @escaping () -> Double = { Date().timeIntervalSince1970 }, log: @escaping (String) -> Void = { print($0) }) throws {
        self.transport = transport; self.maxBytes = maxBytes; self.maxInflight = maxInflight; self.clock = clock; self.log = log
        guard sqlite3_open_v2(path, &db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK else { throw OutboxError.open(path) }
        try exec("PRAGMA journal_mode=WAL"); try exec("PRAGMA synchronous=NORMAL")
        try exec("""
            CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT NOT NULL, seq INTEGER NOT NULL, t_inject INTEGER NOT NULL,
              topic TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)
            """)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path)
        #if os(iOS)
        try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: path)
        #endif
    }

    deinit { sqlite3_close(db) }

    public enum OutboxError: Error { case open(String), sql(String) }

    /// Write to disk, then send if the link is up. Never throws: a disk problem falls back to a plain send.
    public func add(nodeId: String, seq: Int, tInject: Int, topic: String, payload: String) {
        lock.lock(); defer { lock.unlock() }
        do {
            let id = try insert(nodeId, seq, tInject, topic, payload)
            enforceCap()
            drain(fresh: id)
        } catch {
            errors += 1
            log("[outbox] ERROR writing: \(error); sending \(nodeId) seq \(seq) WITHOUT durability")
            if connected { _ = transport.send(topic: topic, payload: wire(payload, replayed: false)) }
        }
    }

    /// The link went up (send what is waiting, oldest first) or down (forget what was in flight; it is sent again next time).
    public func setConnected(_ up: Bool) {
        lock.lock(); defer { lock.unlock() }
        connected = up
        if up { drain(fresh: nil) } else { inflight.removeAll(); inflightIds.removeAll() }
    }

    /// The broker confirmed this message: delete its row and send the next waiting one.
    public func acked(token: Int) {
        lock.lock(); defer { lock.unlock() }
        guard let id = inflight.removeValue(forKey: token) else { staleAcks += 1; return }
        inflightIds.remove(id)
        do { try exec("DELETE FROM outbox WHERE id = \(id)") } catch { errors += 1; log("[outbox] ERROR deleting row \(id): \(error)") }
        drain(fresh: nil)
    }

    public func pending() -> Int {
        lock.lock(); defer { lock.unlock() }
        return Int((try? scalar("SELECT COUNT(*) FROM outbox")) ?? -1)
    }

    // MARK: internals

    private func wire(_ payload: String, replayed: Bool) -> String {
        String(payload.dropLast()) + (replayed ? ", \"replayed\": true" : "") + ", \"t_mqtt_pub\": \(clock())}"
    }

    private func drain(fresh: Int64?) {
        do {
            while connected && inflight.count < maxInflight {
                guard let row = try nextUnsent() else { return }
                let replayed = row.id != fresh
                guard let token = transport.send(topic: row.topic, payload: wire(row.payload, replayed: replayed)) else { return }
                inflight[token] = row.id; inflightIds.insert(row.id)
                if replayed { try exec("UPDATE outbox SET attempts = attempts + 1 WHERE id = \(row.id)") }
            }
        } catch { errors += 1; log("[outbox] ERROR reading the queue: \(error)") }
    }

    private func nextUnsent() throws -> (id: Int64, topic: String, payload: String)? {
        var st: OpaquePointer?
        defer { sqlite3_finalize(st) }
        guard sqlite3_prepare_v2(db, "SELECT id, topic, payload FROM outbox ORDER BY id LIMIT ?", -1, &st, nil) == SQLITE_OK else { throw OutboxError.sql(message()) }
        sqlite3_bind_int(st, 1, Int32(inflightIds.count + 1))
        while sqlite3_step(st) == SQLITE_ROW {
            let id = sqlite3_column_int64(st, 0)
            if !inflightIds.contains(id) { return (id, String(cString: sqlite3_column_text(st, 1)), String(cString: sqlite3_column_text(st, 2))) }
        }
        return nil
    }

    private func insert(_ node: String, _ seq: Int, _ tInject: Int, _ topic: String, _ payload: String) throws -> Int64 {
        var st: OpaquePointer?
        defer { sqlite3_finalize(st) }
        guard sqlite3_prepare_v2(db, "INSERT INTO outbox(node_id, seq, t_inject, topic, payload, created) VALUES (?,?,?,?,?,?)", -1, &st, nil) == SQLITE_OK else { throw OutboxError.sql(message()) }
        let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        sqlite3_bind_text(st, 1, node, -1, transient); sqlite3_bind_int64(st, 2, Int64(seq)); sqlite3_bind_int64(st, 3, Int64(tInject))
        sqlite3_bind_text(st, 4, topic, -1, transient); sqlite3_bind_text(st, 5, payload, -1, transient); sqlite3_bind_double(st, 6, clock())
        guard sqlite3_step(st) == SQLITE_DONE else { throw OutboxError.sql(message()) }
        return sqlite3_last_insert_rowid(db)
    }

    private func usedBytes() throws -> Int {
        Int(try scalar("PRAGMA page_count") - scalar("PRAGMA freelist_count")) * Int(try scalar("PRAGMA page_size"))
    }

    /// Over the cap: delete the oldest rows (in batches) down to 90 % and say so loudly with the lost seq range.
    private func enforceCap() {
        do {
            guard try usedBytes() > maxBytes else { return }
            var lost: [String: [Int]] = [:]
            while try usedBytes() > Int(0.9 * Double(maxBytes)) {
                var st: OpaquePointer?
                guard sqlite3_prepare_v2(db, "SELECT id, node_id, seq FROM outbox ORDER BY id LIMIT 50", -1, &st, nil) == SQLITE_OK else { throw OutboxError.sql(message()) }
                var lastId: Int64 = -1, n = 0
                while sqlite3_step(st) == SQLITE_ROW {
                    lastId = sqlite3_column_int64(st, 0); n += 1
                    lost[String(cString: sqlite3_column_text(st, 1)), default: []].append(Int(sqlite3_column_int64(st, 2)))
                }
                sqlite3_finalize(st)
                if n == 0 { break }
                try exec("DELETE FROM outbox WHERE id <= \(lastId)")
                dropped += n
            }
            for (node, seqs) in lost {
                log("[outbox] FULL (cap \(maxBytes) bytes): dropped \(seqs.count) oldest unsent packets of \(node) (seq \(seqs.min()!)..\(seqs.max()!)); \(dropped) dropped in total. They are LOST; eval/integrity.py will show the gap")
            }
        } catch { errors += 1; log("[outbox] ERROR enforcing the size cap: \(error)") }
    }

    private func message() -> String { String(cString: sqlite3_errmsg(db)) }
    private func exec(_ sql: String) throws { guard sqlite3_exec(db, sql, nil, nil, nil) == SQLITE_OK else { throw OutboxError.sql(message()) } }
    private func scalar(_ sql: String) throws -> Int64 {
        var st: OpaquePointer?
        defer { sqlite3_finalize(st) }
        guard sqlite3_prepare_v2(db, sql, -1, &st, nil) == SQLITE_OK, sqlite3_step(st) == SQLITE_ROW else { throw OutboxError.sql(message()) }
        return sqlite3_column_int64(st, 0)
    }
}
