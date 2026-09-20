# UMP Development Data Plane

The development environment uses the same configurable data-plane layout as
production. The default root is `data/`; production changes only the
`UMP_*_ROOT` environment values to approved mount points.

```
data/
  landing/input/<operator>/<stream>/staging/
  landing/input/<operator>/<stream>/
  landing/input/<operator>/cbs/<substream>/staging/
  landing/input/<operator>/cbs/<substream>/
  processing/
  archive/input/<operator>/<stream>/YYYY/MM/DD/HH/
  landing/output/<downstream>/<operator>/<stream>/staging/
  landing/output/<downstream>/<operator>/<stream>/
  error/
  quarantine/
```

The collector intentionally excludes every directory named `staging`; upstream
must publish files into its stream root before UMP collects them. UMP stages
generated output, verifies it, and atomically publishes it into the downstream
root. Downstream accounts must have no access to `staging`.

Set `SERVICE_MODE=True` to use the collector, decoder, and distributor service
flow. Configure the same root variables in production only after Operations
has approved mount points and permissions.
