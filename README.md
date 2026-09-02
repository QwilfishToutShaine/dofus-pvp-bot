# Bot Discord PvP Dofus Touch

Prototype de bot Discord qui transforme les captures déposées dans deux salons dédiés en
soumissions de points vérifiées par le staff.

## Parcours simplifié

1. Un membre dépose une ou plusieurs images dans le salon normal ou le salon SNG.
2. Le bot lance localement l’analyse OCR des effectifs alliés et adverses.
3. Le bot répond automatiquement avec **Attribuer les points**.
4. L’auteur sélectionne uniquement entre un et quatre membres Discord bénéficiaires.
5. Le bot calcule les points quand l’OCR a abouti et republie la preuve dans le salon de
   vérification. Une analyse indéterminée peut également être envoyée.
6. Le staff valide directement ou saisit les effectifs exacts avec **Corriger les effectifs** ;
   les points sont alors recalculés avant validation.
7. Les effectifs détectés par l’OCR et ceux finalement retenus sont conservés séparément dans
   SQLite, avec le barème et les statuts.
8. Chaque membre peut consulter le classement mensuel avec `/classement`.

Le clic sur **Attribuer les points** reste nécessaire : Discord ne permet pas d’ouvrir
spontanément un formulaire éphémère depuis un simple dépôt de fichier.

## Barème

| Situation | Exemple | Normal | SNG |
| --- | --- | ---: | ---: |
| Égalité d’effectif | 1v1 ou 4v4 | 4 points | 8 points |
| Alliés en infériorité | 1v2 ou 2v3 | 4 points | 8 points |
| Adversaires en infériorité | 3v2 ou 4v3 | 1 point | 2 points |

- Seuls les combats où les adversaires sont en infériorité valent 1 point de base.
- Un combat sans défenseur est donc aussi dans la catégorie à 1 point.
- Attaques et défenses utilisent le même barème.
- Les combats AvA ne sont pas pris en charge.
- Chaque participant sélectionné reçoit la totalité des points.
- Le salon source fixe définitivement le multiplicateur de la soumission.

Le barème se trouve dans [`config/scoring.json`](config/scoring.json). Une soumission conserve la
version et le multiplicateur utilisés lors de son calcul.

## Classement mensuel

- `/classement` affiche le mois en cours, encore provisoire.
- L’option `mois` permet de choisir un mois au format `AAAA-MM` parmi les dix-huit derniers mois.
- Seules les soumissions validées par le staff sont additionnées.
- Chaque bénéficiaire reçoit la totalité des points de la soumission.
- Le nombre de combats est affiché, mais ne départage pas les joueurs.
- Les égalités suivent le classement de compétition : `1, 1, 3`.

Une soumission appartient au mois où elle est **envoyée en vérification**, et non au mois où le
staff clique sur **Valider**. Une validation du 1er octobre peut ainsi compter pour septembre si le
joueur avait terminé sa soumission avant la fin du mois.

Après le changement de mois, le bot attend que les soumissions déjà en vérification soient validées
ou refusées, puis crée une copie définitive et immuable du classement. Cette vérification a lieu
toutes les cinq minutes et au prochain démarrage du bot. Les brouillons jamais envoyés au staff ne
bloquent pas la clôture.

## Analyse automatique

Le détecteur utilise RapidOCR et ONNX Runtime directement sur la machine qui héberge le bot. Il ne
fait appel à aucune API OCR payante. Il repère l’écran de victoire, les sections `Gagnants` et
`Perdants`, associe les noms à leur niveau, puis exclut le percepteur ou le prisme. Plusieurs images
jointes au même message peuvent représenter différentes positions de défilement du même résultat :
les combattants sont alors regroupés sans compter deux fois les lignes communes.

Un seul combat est autorisé par message. Si une capture est illisible, si les durées ne concordent
pas, si l’objectif n’est pas identifié explicitement ou si les effectifs sortent de l’intervalle
attendu, le bot ne devine pas : le staff renseigne les effectifs dans le salon privé de validation.
Le membre qui dépose la capture ne voit aucun champ de correction des effectifs.

Le jeu initial de dix combats fourni pour la calibration est reconnu intégralement, y compris les
photos inclinées et floues. Ce résultat ne constitue pas encore une mesure indépendante de la
précision réelle ; les corrections humaines restent donc conservées afin d’élargir les tests.

## Prérequis

- Python 3.11 ou supérieur ;
- [`uv`](https://docs.astral.sh/uv/) ;
- un serveur Discord sur lequel tu peux créer une application et trois salons.

## 1. Créer l’application Discord

1. Ouvre le [portail développeur Discord](https://discord.com/developers/applications).
2. Clique sur **New Application**, choisis un nom, puis ouvre l’onglet **Bot**.
3. Génère ou réinitialise le token et garde-le secret.
4. Dans **Privileged Gateway Intents**, active **Message Content Intent**.
5. Dans **OAuth2 → URL Generator**, sélectionne les scopes `bot` et `applications.commands`, puis
   les permissions minimales :
   - View Channels ;
   - Send Messages ;
   - Read Message History ;
   - Add Reactions ;
   - Attach Files ;
   - Embed Links.
6. Utilise l’URL générée pour inviter le bot sur le serveur.

Il ne faut pas donner la permission Administrateur au bot.

## 2. Préparer Discord

Crée :

- un salon de captures normal, par exemple `#captures-pvp` ;
- un salon de captures SNG, par exemple `#captures-sng` ;
- un salon privé de validation, par exemple `#validation-pvp` ;
- éventuellement un rôle de membre sélectionnable ;
- un ou plusieurs rôles autorisés à valider.

Les trois salons doivent être distincts. Active le **mode développeur** dans Discord, puis copie
leurs identifiants ainsi que ceux des rôles.

## 3. Configurer le projet

```bash
cp .env.example .env
```

Renseigne ensuite `.env` :

```dotenv
DISCORD_TOKEN=ton-token-secret
NORMAL_SUBMISSION_CHANNEL_ID=identifiant-du-salon-normal
SNG_SUBMISSION_CHANNEL_ID=identifiant-du-salon-sng
REVIEW_CHANNEL_ID=identifiant-du-salon-de-validation
MEMBER_ROLE_ID=identifiant-du-role-de-membre
REVIEWER_ROLE_IDS=identifiant-role-chef,identifiant-role-bras-droit
LEADERBOARD_TIMEZONE=Europe/Paris
```

`MEMBER_ROLE_ID` est facultatif. S’il est renseigné, le bot refuse les utilisateurs qui ne
possèdent pas ce rôle. Les comptes bots sont toujours refusés.

Si `REVIEWER_ROLE_IDS` est vide, seuls les administrateurs du serveur peuvent valider.

Ne publie jamais `.env` et ne copie jamais le token dans le code.

## 4. Installer et lancer

```bash
uv sync
uv run dofus-pvp-bot
```

Le premier traitement OCR peut être plus lent de quelques secondes pendant le chargement des
modèles. Aucun programme OCR séparé, tel que Tesseract, n’est nécessaire.

La base est créée automatiquement dans `data/dofus_pvp.sqlite3`. Les anciennes bases du premier
prototype sont migrées sans suppression de données. Le bot restaure les boutons des brouillons et
des validations encore en attente après un redémarrage.

`LEADERBOARD_TIMEZONE` fixe les limites calendaires du mois et tient compte des changements
d’heure. `Europe/Paris` est la valeur recommandée pour un serveur français.

## 5. Lancer les tests

```bash
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run mypy src
```

## 6. Héberger gratuitement sur Oracle Cloud

Cette procédure installe le bot sur une machine virtuelle Ubuntu et le fait fonctionner en continu
avec `systemd`. Le bot établit lui-même une connexion sortante vers Discord : aucun port applicatif
ou serveur web n'est nécessaire. Seul le port SSH `22` doit être accessible pour administrer la
machine.

Les noms des menus Oracle peuvent légèrement évoluer. Avant de créer la machine, vérifie que la
forme et les ressources choisies portent bien la mention **Always Free eligible** dans ton compte et
ta région.

### 6.1. Créer le réseau Oracle

Dans **Networking → Virtual Cloud Networks**, utilise l'assistant **Create VCN with Internet
Connectivity** avec, par exemple :

- nom du VCN : `dofus-vcn` ;
- bloc CIDR du VCN : `10.0.0.0/16` ;
- sous-réseau public : activé, avec `10.0.0.0/24` ;
- sous-réseau privé : facultatif ;
- IPv6 : facultatif ;
- tags : facultatifs.

L'assistant doit créer une passerelle Internet et une route pour le sous-réseau public. Dans la
liste de sécurité du sous-réseau, conserve une règle entrante TCP sur le port `22`, de préférence
limitée à ton adresse IP si celle-ci est stable. Il n'est pas nécessaire d'ouvrir les ports `80`,
`443` ou un port propre à Discord.

### 6.2. Créer la machine virtuelle

Dans **Compute → Instances → Create instance** :

1. choisis une image Ubuntu 24.04 Minimal ;
2. choisis une forme Ampere A1 compatible avec l'offre gratuite, par exemple
   `VM.Standard.A1.Flex` ;
3. sélectionne le VCN créé précédemment et son **sous-réseau public** ;
4. active **Automatically assign public IPv4 address** ;
5. ajoute ta clé publique SSH ou demande à Oracle de créer une paire de clés ;
6. crée l'instance et attends l'état **Running**.

Conserve la clé privée dans un emplacement sûr. Ne la place ni dans le ZIP du projet ni dans le
dépôt Git. Note ensuite l'adresse **Public IPv4 address** affichée sur la page de l'instance.

### 6.3. Se connecter en SSH depuis Windows

Dans PowerShell, remplace les valeurs entre chevrons :

```powershell
ssh -i "$env:USERPROFILE\.ssh\<nom-de-la-cle>.key" ubuntu@<ADRESSE_IP_PUBLIQUE>
```

Lors de la première connexion, accepte l'empreinte seulement après avoir vérifié que l'adresse IP
correspond bien à celle affichée par Oracle.

### 6.4. Préparer Ubuntu

Sur la machine Oracle :

```bash
sudo apt update
sudo apt install -y unzip curl ca-certificates libxcb1 libgl1
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
uv --version
```

`libxcb1` et `libgl1` sont nécessaires au chargement d'OpenCV utilisé par RapidOCR sur l'image
Ubuntu Minimal. Les avertissements relatifs à `PyNaCl`, à `davey` ou aux fonctions vocales de
Discord sont sans conséquence pour ce bot.

### 6.5. Préparer et transférer le projet

Crée le ZIP depuis le dossier local qui contient bien tes dernières modifications. N'inclus pas
`.venv`, `.env`, les caches, ni la base de données de test. Depuis PowerShell, placé dans le dossier
du projet :

```powershell
Compress-Archive -Force `
  -Path README.md,pyproject.toml,uv.lock,src,tests,config,.env.example `
  -DestinationPath ..\dofus-pvp-bot-deploy.zip

scp -i "$env:USERPROFILE\.ssh\<nom-de-la-cle>.key" `
  "..\dofus-pvp-bot-deploy.zip" `
  ubuntu@<ADRESSE_IP_PUBLIQUE>:/home/ubuntu/
```

Puis, sur la machine Oracle :

```bash
mkdir -p /home/ubuntu/app/dofus-pvp-bot
unzip /home/ubuntu/dofus-pvp-bot-deploy.zip -d /home/ubuntu/app/dofus-pvp-bot
cd /home/ubuntu/app/dofus-pvp-bot
uv sync --frozen --python 3.12
```

Si ton ZIP contient lui-même un dossier `dofus-pvp-bot`, adapte le chemin d'extraction ou déplace
son contenu : le fichier `pyproject.toml` doit finalement se trouver directement dans
`/home/ubuntu/app/dofus-pvp-bot`.

### 6.6. Configurer les secrets sur le serveur

Dans `/home/ubuntu/app/dofus-pvp-bot`, crée `.env` à partir de l'exemple :

```bash
cp .env.example .env
vi .env
chmod 600 .env
```

Dans `vi`, appuie sur `i` pour modifier le fichier, puis sur `Échap` et saisis `:wq` pour enregistrer
et quitter. Renseigne le token et les identifiants du serveur cible selon la section 3. Plusieurs
rôles de validation se séparent par des virgules :

```dotenv
REVIEWER_ROLE_IDS=111111111111111111,222222222222222222
```

Ne mets pas d'espace autour des virgules. Si cette variable reste vide, seuls les administrateurs
Discord peuvent valider.

Teste une première fois le démarrage au premier plan :

```bash
uv run dofus-pvp-bot
```

Une fois le message `Bot connecté` affiché, arrête ce lancement avec `Ctrl+C` avant de créer le
service. Il ne faut jamais laisser deux instances du bot fonctionner simultanément avec le même
token.

### 6.7. Créer le service systemd

Crée le fichier du service :

```bash
sudo vi /etc/systemd/system/dofus-pvp-bot.service
```

Colle le contenu suivant :

```ini
[Unit]
Description=Bot Discord PVP Dofus Touch
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/app/dofus-pvp-bot
ExecStart=/home/ubuntu/app/dofus-pvp-bot/.venv/bin/dofus-pvp-bot
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Active et démarre le service :

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dofus-pvp-bot
sudo systemctl status dofus-pvp-bot
```

L'état attendu est `active (running)`. Le service redémarre automatiquement après une erreur ou un
redémarrage de la VM.

### 6.8. Vérifier le fonctionnement et consulter les logs

```bash
sudo systemctl status dofus-pvp-bot
sudo journalctl -u dofus-pvp-bot -f
```

Avec la seconde commande ouverte, dépose une image de test dans le salon normal. Quitte l'affichage
des logs avec `Ctrl+C` ; cela n'arrête pas le service.

Commandes d'administration courantes :

```bash
sudo systemctl restart dofus-pvp-bot
sudo systemctl stop dofus-pvp-bot
sudo systemctl start dofus-pvp-bot
sudo journalctl -u dofus-pvp-bot -n 100 --no-pager
```

Après toute modification de `.env`, redémarre le service avec `sudo systemctl restart
dofus-pvp-bot`.

### 6.9. Mettre le bot à jour sans perdre le classement

La base persistante se trouve dans `data/dofus_pvp.sqlite3`. Ne la mets pas dans les archives de
déploiement et ne supprime jamais le dossier `data` pendant une mise à jour.

Avant une mise à jour, crée une sauvegarde cohérente pendant que le bot est arrêté :

```bash
sudo systemctl stop dofus-pvp-bot
mkdir -p /home/ubuntu/backups
cp /home/ubuntu/app/dofus-pvp-bot/data/dofus_pvp.sqlite3 \
  /home/ubuntu/backups/dofus_pvp-$(date -u +%Y%m%dT%H%M%SZ).sqlite3
```

Transfère ensuite un nouveau ZIP depuis PowerShell, puis mets à jour uniquement les fichiers du
programme. Si l'archive a été créée comme dans la section 6.5 :

```bash
cd /home/ubuntu/app/dofus-pvp-bot
unzip -o /home/ubuntu/dofus-pvp-bot-deploy.zip -d /home/ubuntu/app/dofus-pvp-bot
uv sync --frozen --python 3.12
sudo systemctl start dofus-pvp-bot
sudo systemctl status dofus-pvp-bot
```

Consulte enfin les logs et réalise une soumission de test. En cas d'échec, la copie présente dans
`/home/ubuntu/backups` permet de restaurer le classement.

### 6.10. Sauvegarder régulièrement SQLite

L'hébergement continu ne remplace pas une sauvegarde. Copie périodiquement la base vers ton PC,
idéalement lorsque le service est arrêté ou après avoir produit une sauvegarde SQLite cohérente :

```powershell
scp -i "$env:USERPROFILE\.ssh\<nom-de-la-cle>.key" `
  ubuntu@<ADRESSE_IP_PUBLIQUE>:/home/ubuntu/backups/<nom-de-la-sauvegarde>.sqlite3 `
  "$env:USERPROFILE\Downloads\"
```

## Structure

```text
src/dofus_pvp_bot/
├── analysis/        # contrat du détecteur et repli sûr
├── application/     # cas d’usage et transitions de statut
├── discord_app/     # événements, sélecteurs et embeds
├── domain/          # catégories et calcul des points
├── storage/         # persistance et migration SQLite
├── config.py        # variables d’environnement
└── __main__.py      # démarrage du bot
```

## Limites actuelles

- La reconnaissance a été calibrée sur un petit jeu initial et doit encore être évaluée sur de
  nouvelles captures indépendantes.
- Le bot doit rester lancé pour détecter les nouvelles captures.
