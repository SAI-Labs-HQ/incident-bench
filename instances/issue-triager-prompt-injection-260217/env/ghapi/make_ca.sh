#!/bin/sh
# Root CA for the runner network, valid from mid-2025 so it also verifies with the runner clock set to early 2026.
set -e
D=${1:-/opt/ghapi/ca}
mkdir -p "$D" && cd "$D"
: > index.txt && echo 01 > serial
cat > ca.cnf <<CNF
[ca]
default_ca=d
[d]
dir=$D
database=\$dir/index.txt
serial=\$dir/serial
new_certs_dir=\$dir
default_md=sha256
policy=p
unique_subject=no
[p]
commonName=supplied
organizationName=optional
[v3_ca]
basicConstraints=critical,CA:TRUE
keyUsage=critical,keyCertSign,cRLSign
subjectKeyIdentifier=hash
CNF
openssl genrsa -out ca.key 2048 2>/dev/null
openssl req -new -key ca.key -subj "/O=Actions Runner Network/CN=Actions Runner Egress CA" -out ca.csr
openssl ca -batch -notext -selfsign -config ca.cnf -keyfile ca.key -extensions v3_ca \
  -startdate 20250601000000Z -enddate 20301231235959Z -in ca.csr -out ca.crt 2>/dev/null
rm -f ca.csr ./*.pem index.txt* serial* ca.cnf
chmod 600 ca.key
