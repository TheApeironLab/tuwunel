# Tuwunel / Element：独立 Vasi 应用部署

本仓库是 Chart、镜像声明、初始化器、接线工具和验收检查的源码。
当前 Chart 候选为 `0.3.0-dev.20261105.1`，保留单副本 `matrix-tuwunel`
StatefulSet（含 backup sidecar）、独立 Element 和默认启用的告警 receiver。
固定五个 AMD64 镜像见 `release/images-amd64.json`；OCI 归档在外部材料目录，不能提交进仓库。

来源见 `files/origin.json` 与 `files/adaptation-origin.json`；构建器另生成
`files/provenance.json`，记录实际 Git 提交、开发标志和输入摘要。

## 已验证范围与版本边界

仓库外 r3 包 `0.3.0-dev.20261104.3` 已于 2026-11-05 在独立 labctl 分支通过
Vasi 执行器安装、真实成员 SSO、无角色拒绝、消息收发、认证媒体上传下载及告警投递。
本候选整合该材料，并修复非空 `egressCidrs` 时 NetworkPolicy 模板作用域错误。
**该新候选须独立部署验收；不能沿用 r3 的实装结果宣称新归档已通过。**

0.2.0 的 `auth.mode: external`、旧 client/Secret 和既有卷不自动接管。
这次候选使用受管 OIDC；存量部署必须审阅身份、凭据与数据迁移，不能直接改 client ID 或 server_name。

## 从公开站点信息生成配置

从 Chentu 公开 handoff `site.json` 或 Vasi `/api/site/integration-site` 取得
公开上下文。示例 `release/site-public.example.json` 使用保留域名，不是可用站点。
仓库命令如下；完整安装包解压后，工具位于包根目录。

```sh
python3 release/prepare-values.py --site-config /external/site-public.json > /external/site-values.json
```

默认 namespace/release 均为 `matrix`。改变它们须同时传 `--namespace/--release`。
首次确定后，`domain`（Matrix server_name）、issuer 和 client ID 不可隐式改写；
重生成时使用 `--existing-values` 核验原身份。values 只含 OIDC/CA/拉取凭据引用，不含密钥。

## 前置条件与安装

1. Traefik CRD、cert-manager、`apeiron-internal-ca` 已就绪，Vasi 站点已接管。
2. 按公开执行器用户名生成 namespace 内最小 Traefik 权限，由平台管理员审阅并应用：

   ```sh
   python3 release/prepare-values.py --site-config /external/site-public.json \
     --executor-user chentu:vasi-executor:CLUSTER_UID \
     --prerequisites-out /external/prerequisites.json > /external/site-values.json
   kubectl apply -f /external/prerequisites.json
   ```

   只授权目标 namespace 的 IngressRoute/Middleware；不创建 CRD，不授予 cluster-admin。
3. 经 Vasi 镜像页/API 导入五个 OCI 归档，repository/tag 必须匹配镜像声明，并核对 digest。
4. 经 Vasi 检查和上传 Chart，检查与部署使用同一份 values；创建 `matrix/matrix` 并开启受管 SSO。
   不启用单入口自动接入，不上传单张应用证书；三个原生入口由 Chart 声明并自动申请证书。
5. 给用户授予 `matrix-user`；安装不会自动授予成员权限。配置集群及客户端的
   `chat.<domain>`、`matrix.<domain>` 和 server_name 根域名解析。
   同集群 IAM/Traefik 默认网络规则可用；外部入口 VIP 需用
   `--iam-egress-cidr VIP/32`（或最小必要 CIDR）显式放行。非默认 HTTPS 端口随站点上下文生成。

Vasi 配发 `vasi-<release>-oidc`、`vasi-<release>-oidc-ca` 和 `vasi-registry-pull`。
Chart 创建/复用自身拥有的 `matrix-appservice` 和 `matrix-alerts`，
不覆写部署方已有凭据；receiver 的 namespace-local initContainer 幂等创建/复用未加密告警房间。
既有 Alertmanager 必须显式复用相同 receiver 密码，不能自动重置或跨 namespace 复制。

## 可用性检查

```sh
python3 release/verify-install.py --values /external/site-values.json \
  --namespace matrix --kubeconfig /explicit/kubeconfig --ca-file /public/site-ca.crt
```

此检查只读验证身份、工作负载、证书、PVC、HTTPS、Element 配置、SSO-only 流程和双方 discovery。
成功不代表真实登录/消息/媒体/恢复均完成；实际功能测试必须另外记录。

## 可选功能

- `alerts.enabled` 默认 true；不要求 observability 已安装。receiver 要求 basic auth。
- `adminSync.enabled` 默认 false。启用需 namespace 本地、受限只读 IAM 目录客户端 Secret
  （`client-id`、`client-secret`），用 `--admin-directory-secret` 显式引用。
  只同步已存在的 Matrix 用户，保留服务账号；IAM 目录读取失败时不写 Matrix。
  Vasi 当前不会自动配发该目录客户端；不能使用普通浏览器 client 或挂载 IAM 管理员凭据。
- `monitoring.enabled` 默认 false，启用还需监控 CRD 与相应 namespace 权限。
- `operations/backup.sh` 保留在线 RocksDB 备份、媒体打包、age 加密和 S3 上传能力。
  显式准备 kubectl/age/s5cmd、`AGE_RECIPIENT`（或兼容的接收方 Secret）、
  namespace 本地 `s3-backup`、可信 CA 和 S3 端点。拒绝未加密备份。
  备份/恢复和可选管理员同步未在本次真实应用验收中验证；Helm rollback 不恢复数据或 IAM。

## 检查与构建

Python 3.11+ 部署工具依赖见 `release/requirements.txt`，Helm 使用 3.19。
构建输出必须在仓库外，正式包要求已提交的干净源码；本地使用 `--development`。

```sh
python3 -m pip install -r release/requirements.txt
python3 -m unittest discover -s tests/chart -p 'test_*.py'
helm lint charts/tuwunel --values charts/tuwunel/values-vasi.example.yaml
python3 release/package.py --output /external/new-chart --development
python3 release/package-install.py --materials /external/verified-material \
  --output /external/new-install --development
```

`--materials` 下的 `images/` 必须与版本化锁文件的字节数、SHA-256、repository/tag/digest 完全一致。
构建器不联网获取镜像；完整包包含 Chart、五个 OCI、接线/只读检查工具及校验清单。
每个版本与输出不可覆盖；发生内容变更须发布新版本。开发来源不是签名或第三方来源认证。
测试日志、凭据、OCI、安装包与 labctl 状态均保留在仓库外。
