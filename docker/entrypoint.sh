#!/bin/sh
# 容器入口：数据目录第一次用就灌入初始名单和种子资产，已有数据一律原样沿用。
#
# 资产中心目前的用户、部门、项目名单来自数据目录里的 directory.json（还没接 fde-server），
# 没有这份名单页面上什么都看不到，所以首次启动必须灌一次。
set -eu

data_dir=${FDE_ASSET_DATA_DIR:-/data}
port=${FDE_ASSET_PORT:-8100}

# 要不要灌种子由服务自己判断（看名单文件和资产仓库里有没有东西）：
# 数据库可能在 PostgreSQL、仓库可能在 Gitea，光看数据目录里有没有某个文件已经不准了
exec python scripts/serve_seeded.py --host 0.0.0.0 --port "$port" --data-dir "$data_dir" \
    --with-worker --seed-if-new
