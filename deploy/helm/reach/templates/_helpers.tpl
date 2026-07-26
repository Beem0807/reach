{{/* Expand the name of the chart. */}}
{{- define "reach.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Fully qualified app name. */}}
{{- define "reach.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "reach.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "reach.labels" -}}
helm.sh/chart: {{ include "reach.chart" . }}
{{ include "reach.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/component: backend
app.kubernetes.io/part-of: reach
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "reach.selectorLabels" -}}
app.kubernetes.io/name: {{ include "reach.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Backend pod selector - the common labels PLUS component=backend, so the backend Service,
     PDB, HPA, and NetworkPolicy match ONLY the backend pods and not the bundled Postgres/Redis
     (which share name+instance). */}}
{{- define "reach.backend.selectorLabels" -}}
{{ include "reach.selectorLabels" . }}
app.kubernetes.io/component: backend
{{- end }}

{{- define "reach.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "reach.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/* The image reference (repository:tag). Tag defaults to the chart appVersion. */}}
{{- define "reach.image" -}}
{{- printf "%s:%s" .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) }}
{{- end }}

{{/* Name of the Secret holding the app's runtime secrets (pepper, signing key, admin
     password, database url, optional metrics token). Points at an existing Secret when
     `config.existingSecret` is set, otherwise a chart-managed one. */}}
{{- define "reach.secretName" -}}
{{- if .Values.config.existingSecret }}
{{- .Values.config.existingSecret }}
{{- else }}
{{- printf "%s-secrets" (include "reach.fullname" .) }}
{{- end }}
{{- end }}

{{/* Name of the ConfigMap holding non-secret env. */}}
{{- define "reach.configMapName" -}}
{{- printf "%s-config" (include "reach.fullname" .) }}
{{- end }}

{{/* Shared env block for the app + migration containers: non-secret config from the
     ConfigMap, app secrets (pepper/session/admin/[metrics]) from the app Secret, and - when the
     bundled Postgres is used - DATABASE_URL from the Postgres Secret (which owns the generated
     password, so DATABASE_URL is built there with the same value). Secret keys are stable
     regardless of whether the app Secret is chart-managed or user-supplied. */}}
{{- define "reach.envFrom" -}}
- configMapRef:
    name: {{ include "reach.configMapName" . }}
- secretRef:
    name: {{ include "reach.secretName" . }}
{{- if (include "reach.usePostgresql" .) }}
- secretRef:
    name: {{ include "reach.postgresql.fullname" . }}
{{- end }}
{{- end }}

{{/* The bundled-Postgres password. Precedence: an explicit postgresql.auth.password, else the
     value already stored in the Postgres Secret (so it's STABLE across upgrades via lookup),
     else a freshly generated one. Capture it ONCE into a variable per render (see
     postgresql.yaml) - calling this twice would generate two different randoms. NOTE: under
     tools that render with `helm template` and no cluster access (e.g. Argo CD), lookup returns
     empty, so an auto-generated password churns on every sync - use config.existingSecret or an
     explicit password for GitOps. */}}
{{- define "reach.postgresql.password" -}}
{{- if .Values.postgresql.auth.password -}}
{{- .Values.postgresql.auth.password -}}
{{- else -}}
{{- $sec := lookup "v1" "Secret" .Release.Namespace (include "reach.postgresql.fullname" .) -}}
{{- if and $sec $sec.data (hasKey $sec.data "POSTGRES_PASSWORD") -}}
{{- index $sec.data "POSTGRES_PASSWORD" | b64dec -}}
{{- else -}}
{{- randAlphaNum 24 -}}
{{- end -}}
{{- end -}}
{{- end }}

{{/* ---- Optional bundled dependencies (Postgres, Redis/Valkey) ---- */}}

{{- define "reach.postgresql.fullname" -}}
{{- printf "%s-postgresql" (include "reach.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "reach.redis.fullname" -}}
{{- printf "%s-redis" (include "reach.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Whether the deployment uses the DynamoDB backend instead of Postgres (AWS-only). */}}
{{- define "reach.isDynamo" -}}
{{- if eq (.Values.config.storageBackend | default "postgres") "dynamo" -}}
true
{{- end -}}
{{- end }}

{{/* Whether to deploy the bundled Postgres: enabled, NOT using DynamoDB, and the operator
     hasn't pointed us at an external DB (config.databaseUrl or a supplied Secret). */}}
{{- define "reach.usePostgresql" -}}
{{- if and .Values.postgresql.enabled (not (include "reach.isDynamo" .)) (not .Values.config.databaseUrl) (not .Values.config.existingSecret) -}}
true
{{- end -}}
{{- end }}

{{/* Whether to deploy the bundled Redis/Valkey: enabled AND no external rate-limit URI given. */}}
{{- define "reach.useRedis" -}}
{{- if and .Values.redis.enabled (not .Values.config.rateLimitStorageUri) -}}
true
{{- end -}}
{{- end }}

{{/* The rate-limit storage URI: an operator-supplied external URI takes precedence; otherwise
     the bundled Redis/Valkey; otherwise empty (in-process per-replica limits). */}}
{{- define "reach.rateLimitUri" -}}
{{- if .Values.config.rateLimitStorageUri -}}
{{- .Values.config.rateLimitStorageUri -}}
{{- else if (include "reach.useRedis" .) -}}
redis://{{ include "reach.redis.fullname" . }}:6379
{{- end -}}
{{- end }}

{{- define "reach.postgresql.labels" -}}
{{ include "reach.selectorLabels" . }}
app.kubernetes.io/component: postgresql
{{- end }}

{{- define "reach.redis.labels" -}}
{{ include "reach.selectorLabels" . }}
app.kubernetes.io/component: redis
{{- end }}
