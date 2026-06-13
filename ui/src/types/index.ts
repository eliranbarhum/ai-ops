export interface RiskFactor {
  severity: 'critical' | 'warning' | 'info'
  message: string
  component: string
}

export interface Evidence {
  source: string
  metric: string
  value: string
  threshold: string | null
}

export interface ScoreEntity {
  name: string
  value: number | string
  unit: string
  status: 'ok' | 'warning' | 'critical'
  threshold?: number
}

export interface SubScore {
  name: string
  label: string
  score: number
  max: number
  pct: number
  status: 'ok' | 'warning' | 'critical'
  critical_count: number
  warning_count: number
  icon: string
  entities?: ScoreEntity[]
  detail?: {
    components: Record<string, string>
    target_version: string
    hcl_results: HclResult[]
    version_gaps: string[]
    interop_gaps: string[]
    sddc_gaps: string[]
    sddc_warnings: string[]
    hcl_warnings: string[]
  }
}

export interface HclResult {
  host: string
  cpu_model: string
  platform: string
  platform_name: string
  esxi_version: string
  certified: boolean | 'warning'
  hcl_url: string
}

export interface AnalysisResponse {
  readiness_score: number
  status: 'READY' | 'WARNING' | 'NOT_READY' | 'UNKNOWN'
  risk_factors: RiskFactor[]
  recommendations: string[]
  evidence: Evidence[]
  explanation: string
  raw_metrics: Record<string, unknown>
  sub_scores?: SubScore[]
  signals_scored?: number
  signals_total?: number
  confidence_note?: string | null
  rollback_risk?: { score: number; level: string; reasons: string[]; host_count: number; vm_count: number; vsan_resync: boolean; blocker_count: number }
}

export interface AnalysisRequest {
  query: string
  target: 'vcf_readiness' | 'capacity' | 'anomaly_detection' | 'network'
}

export interface BulkVmRow {
  name: string
  os: string
  cpu: number
  ram_gb: number
  disk_gb: number
  network: string
  folder: string
  datastore: string
  owner_tag?: string
  env_tag?: string
  _row?: number
  _status?: 'pending' | 'valid' | 'error' | 'creating' | 'done' | 'failed'
  _error?: string
  _vm_id?: string
}

export interface BulkAdUserRow {
  first_name: string
  last_name: string
  username: string
  email: string
  temp_password: string
  ou: string
  groups?: string
  vcenter_role?: string
  _row?: number
  _status?: 'pending' | 'valid' | 'error' | 'creating' | 'done' | 'failed'
  _error?: string
}

export interface GuestVm {
  name: string
  os: string
  hostname: string
  ip: string
  tools: string
  tools_ver: string
  power: string
  cluster: string
  host: string
}
