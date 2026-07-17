# PAVE — Architecture Diagrams (rendered PNGs)

Pre-rendered PNGs of the Mermaid diagram in each `../NN-*.md` view — for pasting into Confluence,
slides, email, or anywhere a Mermaid plugin isn't available. Each PNG corresponds 1:1 to its source
markdown file of the same name.

| PNG | Source |
|-----|--------|
| `01-system-context.png` | [../01-system-context.md](../01-system-context.md) |
| `02-container.png` | [../02-container.md](../02-container.md) |
| `03-request-lifecycle.png` | [../03-request-lifecycle.md](../03-request-lifecycle.md) |
| `04-provisioning-saga.png` | [../04-provisioning-saga.md](../04-provisioning-saga.md) |
| `05-risk-tiered-routing.png` | [../05-risk-tiered-routing.md](../05-risk-tiered-routing.md) |
| `06-governance-tagging-finops.png` | [../06-governance-tagging-finops.md](../06-governance-tagging-finops.md) |
| `07-identity-sod.png` | [../07-identity-sod.md](../07-identity-sod.md) |
| `08-data-model.png` | [../08-data-model.md](../08-data-model.md) |
| `09-hybrid-provisioning.png` | [../09-hybrid-provisioning.md](../09-hybrid-provisioning.md) |
| `10-reconcile-drift.png` | [../10-reconcile-drift.md](../10-reconcile-drift.md) |
| `11-deployment.png` | [../11-deployment.md](../11-deployment.md) |
| `12-backend-components.png` | [../12-backend-components.md](../12-backend-components.md) |
| `13-intake-copilot.png` | [../13-intake-copilot.md](../13-intake-copilot.md) |
| `14-provider-model.png` | [../14-provider-model.md](../14-provider-model.md) |
| `15-approval-esignature.png` | [../15-approval-esignature.md](../15-approval-esignature.md) |
| `16-well-architected.png` | [../16-well-architected.md](../16-well-architected.md) |
| `17-ownership-reassignment.png` | [../17-ownership-reassignment.md](../17-ownership-reassignment.md) |
| `18-record-as-code.png` | [../18-record-as-code.md](../18-record-as-code.md) |
| `19-cost-estimate-escalation.png` | [../19-cost-estimate-escalation.md](../19-cost-estimate-escalation.md) |

## Regenerate

The Mermaid source *is* the `.md` view — these PNGs are derived. To re-render after editing any view
(requires Node; uses `mermaid-cli`, rendered at scale 3 on a white background):

```bash
cd docs/architecture
for md in [0-9]*.md; do
  base="${md%.md}"
  awk '/^```mermaid[ \t]*$/{b=1;next} b&&/^```[ \t]*$/{b=0} b{print}' "$md" > "/tmp/${base}.mmd"
  npx -y @mermaid-js/mermaid-cli@11 -i "/tmp/${base}.mmd" -o "diagrams/${base}.png" -b white -s 3 -e png
done
```

> Rendered and verified with mermaid-cli 11.16 (Chromium via puppeteer). If your Confluence has a
> Mermaid macro, paste the ` ```mermaid ` block from the source `.md` directly; otherwise attach the
> PNG here.
