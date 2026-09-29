# WireGuard VPN — app për Frappe (v2)

App i **pavarur** për qasjen VPN te router-at Teltonika RUT142. S'varet nga asnjë DocType tjetër
(as `Client`, as Client Script, as MQTT): ka DocType-t, faqen dhe workspace-in e vet.

- **VPN Router**: një router për lokacion (emri, kodi, klienti, qyteti, kontakti, IP LAN e pajisjes)
  me Tunnel IP automatike (`10.50.0.0/15`, pa përplasje)
- **Lidhja**: Frappe jep skriptin për CLI-në e router-it; router-i gjeneron vetë çelësin privat
  (s'largohet kurrë nga router-i), ti ngjit public key-in e tij → **Aktivizo** → shtohet te të dy hub-et
- **VPN Dashboard** (`/app/vpn-dashboard`): gjendja e të gjithë router-ave, hub-et (aktiv / rezervë),
  kërkim, filtra, historiku i fundit
- **Njoftime**: Telegram dhe/ose email kur një router bie offline (pas X minutash), kur kthehet, dhe kur bie një hub
- **Import CSV**: shumë lokacione njëherësh nga Excel
- **Historiku** (`VPN Router Event`): krijime, aktivizime, online/offline, revokime, hub-e poshtë
- sinkronizim me **të dy hub-et** me SSH të kufizuar (`wgsync`), kontroll çdo 5 min, reconcile çdo orë

| Pjesa | Ku |
|---|---|
| Workspace | **WireGuard VPN** (sidebar) |
| Dashboard | `/app/vpn-dashboard` |
| Router-at | `/app/vpn-router` |
| Historiku | `/app/vpn-router-event` |
| Konfigurimi | `/app/wireguard-vpn-settings` (vetëm System Manager) |
| Roli | `VPN Manager` — stafi punon me router-at pa qenë System Manager |

---

## 1. Kërkesat

| Çka | Versioni |
|---|---|
| Frappe Framework | **v15** (testuar me 15.121) |
| Python | ≥ 3.10 (ai i bench-it) |
| Redis, worker, scheduler | si në çdo instalim normal të Frappe |
| `openssh-client` (sistem) | për `ssh-keygen` dhe `ssh-keyscan` |

Libraria e vetme Python: **`paramiko >= 3.4`** (SSH te hub-et) — instalohet vetë nga `bench get-app`.
Me dorë: `./env/bin/pip install "paramiko>=3.4"`.

---

## 2. Instalimi i ri

```bash
cd /home/administrator
unzip wireguard_vpn.zip                 # krijon ./wireguard_vpn (me .git brenda)
cd ~/frappe-bench
bench get-app /home/administrator/wireguard_vpn
bench --site SITE install-app wireguard_vpn
bench --site SITE migrate
bench restart                           # vetëm në production (supervisor)
```

Ose nga Gitea/GitHub: `bench get-app https://USER:TOKEN@gitea.firma.com/okosys/wireguard_vpn.git`.

## 2b. Përditësimi nga v1.x (WireGuard Peer + Client) → v2

```bash
cd /home/administrator && rm -rf wireguard_vpn && unzip wireguard_vpn.zip
cd ~/frappe-bench/apps/wireguard_vpn
git pull /home/administrator/wireguard_vpn main
cd ~/frappe-bench
bench --site SITE migrate
bench restart
```

Migrimi bëhet vetë:

- çdo `WireGuard Peer` bëhet `VPN Router` me **të njëjtin Tunnel IP dhe public key** → hub-et s'preken,
  router-at e lidhur mbeten të lidhur
- emri i lokacionit = emri i klientit të vjetër (ndryshoje kur të duash te forma)
- tabela e vjetër ruhet si `_wgvpn_v1_peer_backup` (fshije me dorë kur të jesh i sigurt)
- fshihen Client Script "WireGuard VPN - Buttons" dhe fushat Client / MQTT te Settings
- Settings (hub-et, çelësi, Floating IP, SSH) **mbeten** siç ishin

---

## 3. Çelësi SSH për hub-et (një herë)

Frappe lidhet te hub-et si user-i i kufizuar `wgsync` (mund të ekzekutojë **vetëm** `wg-peer`).
Te serveri Frappe, si user-i i bench-it (te ti: `administrator`):

```bash
ssh-keygen -t ed25519 -N "" -C frappe-wgsync -f ~/.ssh/wgsync_ed25519
cat ~/.ssh/wgsync_ed25519.pub            # → FRAPPE_SYNC_PUBKEY te hub-et (--reconfigure)
ssh-keyscan -t ed25519 46.224.147.121 2.28.114.90 > ~/.ssh/known_hosts_wg
ssh -i ~/.ssh/wgsync_ed25519 -o UserKnownHostsFile=~/.ssh/known_hosts_wg wgsync@46.224.147.121 ping
# pritet:  pong hub-1 peers=N iface_up=1
```

---

## 4. Konfigurimi — WireGuard VPN Settings

| Fusha | Vlera |
|---|---|
| Hub-et | `46.224.147.121` dhe `2.28.114.90` (një për rresht) |
| Hub Public Key | `6Ye0QCm/egznoQKyjeUdCCJB+cvz5X01JVFNBfiP2BQ=` |
| Endpoint Host / Port | `46.225.250.72` (Floating IP) / `51820` |
| SSH User / Key / Known Hosts | `wgsync` · `/home/administrator/.ssh/wgsync_ed25519` · `/home/administrator/.ssh/known_hosts_wg` |
| Port-forward te pajisja LAN | `[{"name": "ssh", "ext_port": 2222, "int_port": 22}]` |
| Offline pas (sekonda) | `300` |

**Testo → Testo hub-et** → kur janë OK, vendos **Aktiv** ✓ dhe ruaje. Scheduler-i duhet të jetë ndezur:
`bench --site SITE enable-scheduler`.

### Njoftimet (opsionale)

| Fusha | Shpjegim |
|---|---|
| Dërgo njoftime | ✓ |
| Njofto pasi është offline (minuta) | default `10` — shmang alarmet kur LTE rilidhet për pak |
| Njofto edhe kur kthehet online | ✓ (dërgohet vetëm nëse u dërgua më parë "offline") |
| Listo me detaje deri në | default `5` — mbi këtë, një mesazh i vetëm përmbledhës |
| Telegram Bot Token / Chat ID | nga @BotFather; Chat ID i grupit (p.sh. `-100…`) |
| Email-at | një për rresht (kërkon Email Account dalës në Frappe) |

**Testo → Testo njoftimet** dërgon një mesazh prove. Për router-a specifikë: ✓ *Pa njoftime për këtë router*.

Telegram Chat ID: shto bot-in në grup, shkruaj një mesazh, hap
`https://api.telegram.org/bot<TOKEN>/getUpdates` dhe merr `chat.id`.

---

## 5. Lidhja e një router-i

1. **VPN Dashboard → Shto router** (ose Import CSV). Plotëso *Emri i lokacionit* → **Save**.
   Tunnel IP ndahet vetë (p.sh. `10.50.1.5`), statusi **Pending**.
2. Kliko **Lidh router-in** → **Kopjo skriptin**.
3. `ssh root@192.168.1.1` (ose WebUI → System → Administration → CLI) → ngjite krejt skriptin.
4. Në fund router-i shfaq **Router Public Key** → ngjite te dialogu → **Aktivizo**.
5. Frappe e shton te hub-1 dhe hub-2 → **Active**. **VPN → Kontrollo tani** tregon handshake-un.

Pa SSH: dialogu ka tabelën me vlerat për WebUI (Services → VPN → WireGuard).
Router i resetuar/ndërruar: ekzekuto skriptin sërish dhe aktivizo çelësin e ri (i vjetri hiqet nga hub-et).

Qasja nga stafi (Tailscale, user `support-team`, `--accept-routes`):
`ssh root@10.50.1.5`, `https://10.50.1.5`, pajisja pas router-it `ssh -p 2222 USER@10.50.1.5`.

### Statuset

| Statusi | Kuptimi |
|---|---|
| Pending | pritet public key-i i router-it |
| Active (Online) | te hub-et, handshake më i ri se "Offline pas" |
| Offline | te hub-et, pa handshake të freskët (router-i fikur / pa internet) |
| Sync Error | një hub s'u arrit — riprovohet vetë çdo 5 min |
| Revoked | qasja u hoq; *Skripti i heqjes* e pastron edhe router-in |

---

## 6. Import CSV

Dashboard → **Import CSV** (ose lista e router-ave → Import CSV). Nga Excel: **Ruaje si → CSV UTF-8**
(ndarësi `,` `;` ose TAB njihet vetë). Kolona e detyrueshme: `site_name` (ose *Emri* / *Lokacioni*).
Opsionale: `site_code`, `customer`, `city`, `address`, `contact_name`, `contact_phone`, `router_serial`,
`device_lan_ip`, `notes`. **Kontrollo** tregon çka krijohet / kapërcehet / gabimet para se të importohet.

---

## 7. Terminal

```bash
bench --site SITE wireguard-vpn-check                                   # SSH te hub-et
bench --site SITE execute wireguard_vpn.vpn.check_now                   # handshake tani
bench --site SITE execute wireguard_vpn.vpn.job_reconcile --kwargs "{'force': True}"
bench --site SITE execute wireguard_vpn.vpn.job_reconcile --kwargs "{'max_drop': 100, 'force': True}"  # pas revokimit masiv
```

## 8. Probleme të shpeshta

| Simptoma | Shkaku / zgjidhja |
|---|---|
| `Testo hub-et`: `AuthenticationException` | çelësi publik s'është te hub-et → `--reconfigure` (FRAPPE_SYNC_PUBKEY) |
| `Testo hub-et`: `not found in known_hosts` | rikrijo `known_hosts_wg` me `ssh-keyscan` |
| `Testo hub-et`: `timed out` | IP-ja e Frappe s'lejohet te hub-et (ufw / Hetzner Firewall) |
| Hub-et "pa të dhëna ende" te Dashboard | kliko **Kontrollo tani**; kontrollo që *Aktiv* ✓ dhe scheduler-i punon |
| Active pas aktivizimit, pastaj Offline | router-i s'arrin `46.225.250.72:51820/udp` ose çelësi i ngjitur s'është ai i router-it |
| S'vijnë njoftime | *Testo njoftimet*; shih `/app/error-log` ("VPN: njoftimi dështoi") |

Log-et: `/app/error-log`, `logs/worker.error.log`, `logs/scheduler.log`.

Licenca: MIT.
