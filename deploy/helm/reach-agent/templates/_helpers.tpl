{{/* Expand the name of the chart. */}}
{{- define "reach-agent.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Fully qualified app name. */}}
{{- define "reach-agent.fullname" -}}
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

{{- define "reach-agent.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "reach-agent.labels" -}}
helm.sh/chart: {{ include "reach-agent.chart" . }}
{{ include "reach-agent.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "reach-agent.selectorLabels" -}}
app.kubernetes.io/name: {{ include "reach-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "reach-agent.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "reach-agent.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/* Name of the Secret holding the bootstrap (api-url/install-token). */}}
{{- define "reach-agent.bootstrapSecretName" -}}
{{- if .Values.reach.existingSecret }}
{{- .Values.reach.existingSecret }}
{{- else }}
{{- printf "%s-bootstrap" (include "reach-agent.fullname" .) }}
{{- end }}
{{- end }}

{{/* Name of the Secret the agent uses to persist its claimed agent_token. */}}
{{- define "reach-agent.tokenSecretName" -}}
{{- default (printf "%s-token" (include "reach-agent.fullname" .)) .Values.tokenSecretName }}
{{- end }}

{{/* Lease name for leader election. */}}
{{- define "reach-agent.leaseName" -}}
{{- default (include "reach-agent.fullname" .) .Values.leaderElection.leaseName }}
{{- end }}
