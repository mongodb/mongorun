========
Releases
========

The versioning standard in mongorun is pretty straight-forward. It follows a
major.minor.micro version system. We decided to start mtools at version 1.0.0
as it was already pretty stable and usable at that stage.

Bug fixes and minor feature additions are released periodically as part of
micro releases. Minor releases will include more significant changes, including
interface changes. We're not sure yet what a major release constitutes :-).

The development version has the suffix ``dev0`` attached. For example
``1.0.1-dev0`` is the development version leading up to a ``1.0.1`` release.

Minor and major releases might have release candidates leading up to the final
release. Those are marked with the suffix ``rcX`` where ``X``
is a number starting at 0. For example, version ``1.2.0-rc2`` is the 3rd
release candidate for upcoming release version ``1.2.0``.

GitHub milestones and git tags are named with a prefix ``v``, for example
``v1.0.1``.


Releasing a new version
~~~~~~~~~~~~~~~~~~~~~~~

#. Increase the version in ``./mrun/util/version.py`` from ``x.y.z-dev0`` to
   ``x.y.z`` in develop.
#. Make sure tests are passing in Python 3.10+ via
   ``tox -re py310,py311,py312,py313,py314``.
#. Make sure documentation tests are passing via ``tox -re doc``.
#. Update README.rst and CHANGES.rst accordingly.
#. Any other cleanup tasks.
#. Merge the ``develop`` branch into ``main`` with --ff-only (docs rebuild
   trigger)
#. Tag the release commit with the new version (PyPI publish trigger)
#. Bump the version on the develop branch (in ``./mrun/util/version.py``) to
   ``x.y.(z+1)-dev0``.
