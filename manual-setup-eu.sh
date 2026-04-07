#!/bin/bash
set -e

echo "Installing Docker Compose..."
sudo mkdir -p /usr/local/bin
sudo curl -L "https://github.com/docker/compose/releases/download/v2.24.5/docker-compose-linux-x86_64" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

echo "Verifying Docker Compose..."
/usr/local/bin/docker-compose --version

echo "Setting up directories..."
sudo mkdir -p /var/lib/postgresql/data
sudo mkdir -p /var/lib/redis/data
sudo mkdir -p /docker-entrypoint-initdb/

# Copy pre-init script if exists
if [ -f /tmp/pre-init.sql ]; then
    sudo cp /tmp/pre-init.sql /docker-entrypoint-initdb.d/
fi

echo "Starting Docker services..."
cd /tmp
sudo /usr/local/bin/docker-compose -f docker-compose.yml up -d

echo "Waiting for services to start..."
sleep 20

echo "Checking containers..."
sudo docker ps

echo "✅ Setup complete!"
