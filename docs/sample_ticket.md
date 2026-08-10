# Sample SOC Incident Ticket

> Generated with the deterministic template renderer from
> [`evaluation/holdout/shap_example_output.json`](evaluation/holdout/shap_example_output.json).
> It contains only validated detector evidence; no LLM-authored interpretation is included.

## 1. Incident Summary

Connection telemetry from `192.168.1.47` was flagged as suspicious at `2026-08-10T00:00:00+00:00`. Random Forest classified the activity as `dos` with 100.0% family confidence. Isolation Forest also measured 82.6% anomaly risk; the fused anomaly confidence was 93.0%, requiring analyst validation and containment triage.

## 2. Attack Classification

- Type: `dos`
- Random Forest family confidence: 100.0%
- Isolation Forest anomaly risk: 82.6%
- Fused anomaly confidence: 93.0%

## 3. Why Flagged - Evidence

- Isolation Forest signal: risk 82.6% (configured threshold 70.0%); raw decision score `-0.111155`, where lower values are more anomalous.
- `flag_S0`: observed value `1.0`; supports the predicted class.
- `dst_host_srv_serror_rate`: observed value `1.0`; supports the predicted class.
- `dst_host_serror_rate`: observed value `1.0`; supports the predicted class.
- `serror_rate`: observed value `1.0`; supports the predicted class.
- `srv_serror_rate`: observed value `1.0`; supports the predicted class.

## 4. Immediate Containment Steps

1. Validate whether `192.168.1.47` maps to an expected internal asset or approved scanner.
2. Review recent authentication, connection, and firewall events involving the source and destination pair.
3. Temporarily restrict the source if the activity is unauthorized or recurring.
4. Preserve packet, flow, and endpoint evidence before remediation.

## 5. Investigation Queries

```spl
index=network sourcetype=firewall src_ip="192.168.1.47" earliest=-24h
| stats count, values(action), values(dest_ip), values(dest_port), values(app) by src_ip
```

```spl
index=network sourcetype=ids src_ip="192.168.1.47" earliest=-24h
| table _time signature severity src_ip dest_ip dest_port
```

## 6. Escalation Recommendation

P2 - Escalate to the SOC lead if the traffic is not attributable to approved scanning, backup, or administrative activity. Raise to P1 if the same source shows confirmed exploitation, lateral movement, or impact on production services.
