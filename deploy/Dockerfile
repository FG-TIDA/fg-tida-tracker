FROM node:24-bookworm-slim@sha256:c86ecead9e55a5210ac1727de38a4ca840bf03dcaf34dc9749addbf383b1920c AS frontend
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY client ./client
COPY dev/vite-plugins ./dev/vite-plugins
COPY vite.config.js ./
COPY .sassrc.json ./
COPY ietf/static ./ietf/static
COPY ietf/secr/static ./ietf/secr/static
RUN npm run build && npm run legacy:build

FROM python:3.12-slim-trixie@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_SETTINGS_MODULE=ietf.settings FGTIDA_RELEASE_VERSION=1.0.0-fgtida
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev libmagic1 libpango-1.0-0 libpangoft2-1.0-0 libcairo2 \
    libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info patch ca-certificates memcached \
    && rm -rf /var/lib/apt/lists/*
COPY deploy/requirements-runtime.lock ./requirements-runtime.lock
RUN python -m pip install --no-cache-dir --upgrade pip==26.3 \
    && python -m pip install --no-cache-dir -r requirements-runtime.lock
COPY ietf ./ietf
COPY patch ./patch
COPY debug.py ./
RUN python -c "import pathlib, django; from ietf.utils import patch; root=pathlib.Path(django.__file__).parent.parent; names=['change-oidc-provider-field-sizes-228.patch','fix-oidc-access-token-post.patch','fix-jwkest-jwt-logging.patch','tastypie-django22-fielderror-response.patch']; assert all(patch.fromfile(pathlib.Path('patch')/name).apply(root=bytes(root)) for name in names)"
COPY deploy/gunicorn.conf.py ./deploy/gunicorn.conf.py
COPY deploy/entrypoint.sh ./deploy/entrypoint.sh
COPY deploy/render_start.py deploy/render_bootstrap.py ./deploy/
RUN chmod 755 deploy/entrypoint.sh
COPY --from=frontend /build/ietf/static/dist ./ietf/static/dist
COPY --from=frontend /build/ietf/static/dist-neue ./ietf/static/dist-neue
COPY --from=frontend /build/ietf/secr/static/dist ./ietf/secr/static/dist
RUN useradd --uid 10001 --create-home --shell /bin/sh tracker \
    && (getent group 1000 || groupadd --gid 1000 render-secrets) \
    && usermod -a -G "$(getent group 1000 | cut -d: -f1)" tracker \
    && install -d -m 700 -o tracker -g tracker /home/tracker/.ssh \
    && mkdir -p /data/media/photo /data/proceedings /data/documents /data/charters /data/static \
    && chown -R tracker:tracker /data
COPY deploy/requirements-production-extra.txt ./requirements-production-extra.txt
RUN pip install --no-cache-dir -r requirements-production-extra.txt
# Browser automation is a development/test dependency, not a server dependency.
RUN python -m pip uninstall -y selenium
# Retain runtime database/PDF libraries, not compilers or MIME indexing tools.
RUN apt-mark manual libpq5 \
    && apt-get purge -y --auto-remove build-essential libpq-dev libffi-dev shared-mime-info libgdk-pixbuf-2.0-0 patch \
    && rm -rf /var/lib/apt/lists/*
# Installers are build tools, not part of the immutable application runtime.
# Remove their vendored libraries and the older ensurepip bootstrap wheel too.
RUN python -m pip check \
    && python -m pip uninstall -y pip \
    && python -c "import pathlib, shutil, sysconfig; p=pathlib.Path(sysconfig.get_path('stdlib'))/'ensurepip'; assert str(p).startswith('/usr/local/lib/python3.12/'); shutil.rmtree(p)"
ENV XDG_CACHE_HOME=/tmp/cache
USER tracker
EXPOSE 8000
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["gunicorn", "--config", "deploy/gunicorn.conf.py", "ietf.wsgi:application"]
