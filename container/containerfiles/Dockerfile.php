# PHP major.minor, set by PHP_VER in docker-compose.yml:
#   docker compose up -d --build              # PHP 8.5, the default
#   PHP_VER=7.4 docker compose up -d --build  # another version, e.g. the one
#                                             # an imported site ran on
ARG PHP_VER=8.5
FROM php:${PHP_VER}-apache

# Enable Apache rewrite module
RUN a2enmod rewrite

# Set ServerName to suppress warning
RUN echo "ServerName localhost" >> /etc/apache2/apache2.conf

# Permanent permission fix: host files in ./src are owned by UID/GID 1000
# with mode 700 (owner-only). The bind mount shadows image permissions, so
# no RUN chmod here could ever fix that — instead Apache's www-data is
# remapped to the host owner's UID/GID, making it the file owner.
ARG HOST_UID=1000
ARG HOST_GID=1000
RUN groupmod -g ${HOST_GID} www-data && usermod -u ${HOST_UID} www-data

# PHP settings for an old codebase on a newer PHP (hide E_DEPRECATED in browser)
COPY containerfiles/php-engine.ini /usr/local/etc/php/conf.d/php-engine.ini

# Set working directory (code comes from the ./src bind mount at runtime,
# so we intentionally do NOT COPY src/ here — it was a 2.8 GB build context
# that stalled builds and got shadowed by the mount anyway)
WORKDIR /var/www/html

EXPOSE 80
