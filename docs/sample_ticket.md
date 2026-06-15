# Sample SOC Incident Ticket

## 1. Incident Summary

Connection telemetry from `192.168.1.47` was flagged as suspicious by the dual-detector pipeline. The Random Forest classified the activity as `dos` with 98.2% confidence, and the Isolation Forest score was also anomalous, making this a high-confidence triage candidate.

## 2. Attack Classification

- Type: Denial of Service (`dos`)
- Model confidence: 98.2%
- Detection source: Random Forest classifier plus Isolation Forest anomaly scoring

## 3. Why Flagged - Evidence

- `service_http` was active, indicating the connection targeted web-facing traffic.
- `count` and `srv_count` were both 511, showing a very high concentration of recent connections.
- `same_srv_rate` was 1.0, meaning the traffic repeatedly targeted the same service.
- `dst_host_srv_count` was 255, indicating repeated activity against the destination service.
- `dst_bytes` was 0, which is consistent with traffic that did not complete a normal request-response pattern.

## 4. Immediate Containment Steps

1. Validate whether `192.168.1.47` is an approved scanner, load tester, or internal monitoring host.
2. Review firewall, IDS, and web server logs for repeated destination-service hits from the same source.
3. Rate-limit or temporarily block the source if the activity is unauthorized.
4. Preserve flow records and packet captures for post-incident review.

## 5. Investigation Queries

```spl
index=network sourcetype=firewall src_ip="192.168.1.47" earliest=-24h
| stats count, values(action), values(dest_ip), values(dest_port), values(app) by src_ip
```

```spl
index=web OR index=network src_ip="192.168.1.47" earliest=-24h
| timechart span=5m count by dest_ip
```

## 6. Escalation Recommendation

P2 - Escalate to the SOC lead if this activity is not tied to approved testing or scheduled scanning. Raise to P1 if the same source is associated with service degradation, exploitation attempts, or confirmed business impact.
