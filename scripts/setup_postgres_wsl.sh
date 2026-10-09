#!/bin/bash
set -e
su - postgres -c "psql -c \"ALTER USER postgres WITH PASSWORD 'postgres';\""
echo "local   all             all                                     trust" > /etc/postgresql/18/main/pg_hba.conf
echo "host    all             all             127.0.0.1/32            trust" >> /etc/postgresql/18/main/pg_hba.conf
echo "host    all             all             ::1/128                 trust" >> /etc/postgresql/18/main/pg_hba.conf
echo "host    all             all             0.0.0.0/0               trust" >> /etc/postgresql/18/main/pg_hba.conf
service postgresql restart
