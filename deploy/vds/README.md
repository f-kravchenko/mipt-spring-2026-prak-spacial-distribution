# VDS deployment

This path is for a single VDS that pulls our controlled production repository
and runs the service with Docker Compose. The developer repository stays as a
read-only upstream; production secrets live only on the VDS.

## Server size

Recommended minimum:

- 4 vCPU
- 8 GB RAM
- 80 GB NVMe
- Ubuntu 22.04/24.04

The first ETL load is CPU and disk intensive. After ETL, runtime services are
much lighter.

## First server setup

Install Docker on the VDS:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
```

Log out and back in after `usermod`, then clone the controlled production
repository or branch, not the read-only developer upstream:

```bash
sudo mkdir -p /opt/mipt-deaggr
sudo chown "$USER":"$USER" /opt/mipt-deaggr
git clone <YOUR_CONTROLLED_REPO_URL> /opt/mipt-deaggr/app
cd /opt/mipt-deaggr/app
git checkout vds-deploy
```

Keep the developer repository configured locally as `upstream` and merge its
updates into the controlled production branch before deploying:

```bash
git remote add upstream https://git.kravchenko.cloud/fedor/mipt-deaggr
git fetch upstream
git merge upstream/main
```

Do not deploy directly from the read-only upstream: deploy files and hotfixes
that exist only on the server will be lost or conflict on update.

## Environment

```bash
cp .env.prod.example .env.prod
nano .env.prod
```

For first launch by server IP:

```dotenv
SITE_HOST=:80
PUBLIC_BASE_URL=http://YOUR_SERVER_IP
```

For a real domain with automatic HTTPS:

```dotenv
SITE_HOST=maps.example.com
PUBLIC_BASE_URL=https://maps.example.com
```

Point the domain A record to the VDS before switching to HTTPS mode.

## First launch

Start infrastructure and app:

```bash
./deploy/vds/deploy.sh
```

Load data once:

```bash
./deploy/vds/run-etl.sh
```

Open:

```text
http://YOUR_SERVER_IP
```

or your HTTPS domain.

## Automatic updates

The VDS can poll the controlled `main` branch and deploy new commits without
manual SSH commands. Install the timer once on the server:

```bash
./deploy/vds/install-auto-deploy.sh
```

It checks GitHub every five minutes. The timer only deploys application and
infrastructure changes; ETL remains a separate manual operation.

Check the timer and recent deployment logs:

```bash
systemctl status mipt-deaggr-auto-deploy.timer
journalctl -u mipt-deaggr-auto-deploy.service -n 100 --no-pager
```

## Manual updates

For normal code updates:

```bash
cd /opt/mipt-deaggr/app
./deploy/vds/deploy.sh
```

ETL is intentionally separate. Run it only after data, masks, or ETL logic
changes:

```bash
./deploy/vds/run-etl.sh
```

## Notes

- The VDS pulls from our controlled production repository.
- The developer repository is read-only upstream for controlled syncs.
- Real passwords stay in `.env.prod` on the server.
- Docker volumes keep Postgres and Caddy certificates between deploys.
- `deploy.sh` runs SQL migrations before restarting app services.
