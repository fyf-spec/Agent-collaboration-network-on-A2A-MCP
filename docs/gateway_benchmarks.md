# MCP Gateway governance benchmarks

This benchmark suite measures what the MCP Gateway changes compared with direct
agent-to-MCP calls. The experiments are intentionally local and deterministic so
the figures can be regenerated for reports or posters.

## Command

```powershell
python scripts\gateway_benchmarks.py --repeats 3
```

For faster iteration:

```powershell
python scripts\gateway_benchmarks.py --quick --repeats 2
```

To regenerate figures from existing raw samples:

```powershell
python scripts\gateway_benchmarks.py --plot-only
```

Outputs are written to `results/gateway_benchmarks/`.

## Experiments

| Experiment | Question | Primary metric |
| --- | --- | --- |
| Cache reuse | Does a TTL cache prevent repeated identical tool calls from reaching MCP? | Upstream MCP calls vs. repeated requests |
| Request coalescing | Does a duplicate burst collapse behind one in-flight upstream request? | Upstream MCP calls vs. concurrent duplicates |
| Backpressure | Does the Gateway bound accepted upstream work under concurrent unique requests? | Accepted vs. rate-limited requests |
| Circuit breaker | Does the Gateway fail fast after repeated upstream timeouts? | Per-request latency and circuit-open count |

## Interpretation

The Gateway improves traffic governance rather than model quality. The expected
effect is lower upstream amplification, bounded concurrency, and faster recovery
behavior when MCP servers are slow or failing. JSON-RPC request semantics are
preserved: the Gateway remains a transparent control point between agents and
MCP servers.
