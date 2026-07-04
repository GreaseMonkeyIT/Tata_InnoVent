{{- define "factory.resources" -}}
resources:
  requests: { cpu: {{ .cpu.req }}, memory: {{ .mem.req }} }
  limits:   { cpu: {{ .cpu.lim }}, memory: {{ .mem.lim }} }
{{- end -}}

{{- define "factory.env" -}}
{{- if .env }}
env:
{{- range $k, $v := .env }}
  - { name: {{ $k }}, value: {{ $v | quote }} }
{{- end }}
{{- end }}
{{- end -}}

{{- define "factory.mounts" -}}
{{- if or .mounts .tmpfs }}
volumeMounts:
{{- range .mounts }}
  - { name: {{ .pvc }}, mountPath: {{ .path }} }
{{- end }}
{{- if .tmpfs }}
  - { name: cache, mountPath: /cache }
{{- end }}
{{- end }}
{{- end -}}

{{- define "factory.volumes" -}}
{{- if or .mounts .tmpfs }}
volumes:
{{- range .mounts }}
  - name: {{ .pvc }}
    persistentVolumeClaim: { claimName: {{ .pvc }} }
{{- end }}
{{- if .tmpfs }}
  - name: cache
    emptyDir: { medium: Memory, sizeLimit: {{ .tmpfs }} }
{{- end }}
{{- end }}
{{- end -}}

{{- define "factory.affinity" -}}
{{- if .affinityGroup }}
affinity:
  podAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      - topologyKey: kubernetes.io/hostname
        labelSelector:
          matchLabels: { affinity-group: {{ .affinityGroup }} }
{{- end }}
{{- end -}}
