"""Service- and operator-aware database router.

Two planes:

* **Control plane** (``default``): operator-agnostic data — auth/admin/sessions,
  ``core`` (incl. JobRecord + User), ``collection`` (CDRFile/DataSource —
  globally addressable by id), ``reference``, ``portals``, ``dashboard``,
  ``api``, ``processing``, ``businesslogic``.
* **Per-operator data plane** (``mediation_{operator}``): the decoded CDR record
  tables — apps ``msc/ims/pgw/sgsn/sgw/cbs``. The active operator comes from
  :func:`core.operator_context.get_operator` (set by the supervisor per file and
  by request middleware per web request).
* **Per-service plane** (unchanged): ``interconnect`` / ``regulatory`` /
  ``roaming`` keep their existing dedicated databases.

Cross-plane ForeignKeys (e.g. a record's ``file`` -> collection.CDRFile in
``default``) use ``db_constraint=False`` so PG never tries to enforce them
across databases.
"""
from django.conf import settings

# app_label -> fixed (non-operator) service DB alias.
SERVICE_DB = {
    'interconnect': 'interconnect',
    'regulatory':   'regulatory',
    'roaming':      'roaming',
}

# Decoded-record apps that live in the per-operator mediation_{op} database.
STREAM_LABELS = {'msc', 'ims', 'pgw', 'sgsn', 'sgw', 'cbs'}


def _mediation_alias() -> str:
    """Pick the decoded-record DB for the active operator.

    The home/primary operator (``settings.DEFAULT_OPERATOR``) keeps using the
    existing ``default`` database, so all current data and queries are
    unchanged. Every *other* operator gets its own ``mediation_{op}`` database.
    Falls back to ``default`` if an operator DB has not been provisioned yet.
    """
    from core.operator_context import get_operator

    op = get_operator()
    if op == getattr(settings, 'DEFAULT_OPERATOR', 'orange'):
        return 'default'
    alias = f'mediation_{op}'
    return alias if alias in settings.DATABASES else 'default'


class ServiceRouter:
    """Map (model.app_label [+ active operator]) -> physical database alias."""

    def _db_for(self, model):
        label = model._meta.app_label
        if label in SERVICE_DB:
            return SERVICE_DB[label]
        if label in STREAM_LABELS:
            return _mediation_alias()
        return 'default'

    def db_for_read(self, model, **hints):
        return self._db_for(model)

    def db_for_write(self, model, **hints):
        return self._db_for(model)

    def allow_relation(self, obj1, obj2, **hints):
        """Soft cross-DB FKs (db_constraint=False) are allowed."""
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """Each app migrates only into its own plane.

        * stream apps -> any ``mediation_*`` alias (per-operator)
        * interconnect/regulatory/roaming -> their fixed alias
        * everything else -> ``default``
        """
        if app_label in STREAM_LABELS:
            # Stream tables live in `default` (home operator) AND every
            # per-operator mediation DB.
            return db == 'default' or db.startswith('mediation_')
        if app_label in SERVICE_DB:
            return db == SERVICE_DB[app_label]
        # Control-plane apps live only in default; never in operator/service DBs.
        return db == 'default'
