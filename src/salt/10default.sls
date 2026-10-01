{% set config = salt['omv_conf.get']('conf.service.protondrive') %}
{% set sets = salt['omv_conf.get']('conf.service.protondrive.set') %}

protondrive_idle:
  cmd.run:
    - name: /usr/sbin/omv-protondrive check-idle

protondrive_state:
  file.directory:
    - name: /var/lib/openmediavault-protondrive
    - user: root
    - group: protondrive
    - mode: '0750'
    - require:
      - cmd: protondrive_idle

protondrive_session_state:
  file.directory:
    - name: /var/lib/openmediavault-protondrive/proton
    - user: protondrive
    - group: protondrive
    - mode: '0700'
    - require:
      - file: protondrive_state

protondrive_staging:
  file.directory:
    - name: {{ config.stagingpath | json }}
    - user: root
    - group: protondrive
    - mode: '0750'
    - makedirs: True
    - require:
      - cmd: protondrive_idle

protondrive_config:
  file.managed:
    - name: /etc/openmediavault/protondrive.json
    - source: salt://{{ tpldir }}/files/config.json.j2
    - template: jinja
    - context:
        config: {{ config | json }}
        sets: {{ sets | json }}
    - user: root
    - group: protondrive
    - mode: '0640'
    - require:
      - cmd: protondrive_idle

protondrive_validate:
  cmd.run:
    - name: /usr/sbin/omv-protondrive validate
    - require:
      - file: protondrive_config

{% for unit in ['protondrive.service', 'protondrive-backup.service', 'protondrive-backup.timer', 'protondrive-recover.service'] %}
protondrive_unit_{{ loop.index }}:
  file.managed:
    - name: /etc/systemd/system/omv-{{ unit }}
    - source: salt://{{ tpldir }}/files/{{ unit }}.j2
    - template: jinja
    - context:
        config: {{ config | json }}
        sets: {{ sets | json }}
    - mode: '0644'
    - require:
      - cmd: protondrive_validate
{% endfor %}

protondrive_reload:
  cmd.run:
    - name: systemctl daemon-reload
    - onchanges:
{% for n in range(1, 5) %}
      - file: protondrive_unit_{{ n }}
{% endfor %}

protondrive_recovery_enabled:
  service.enabled:
    - name: omv-protondrive-recover
    - require:
      - cmd: protondrive_reload

protondrive_daemon:
  service.running:
    - name: omv-protondrive
    - enable: True
    - require:
      - file: protondrive_session_state
      - file: protondrive_staging
      - cmd: protondrive_reload
    - watch:
      - file: protondrive_config
      - file: protondrive_unit_1

protondrive_timer:
{% if config.enable | to_bool %}
  service.running:
    - name: omv-protondrive-backup.timer
    - enable: True
    - watch:
      - file: protondrive_unit_3
{% else %}
  service.dead:
    - name: omv-protondrive-backup.timer
    - enable: False
{% endif %}
    - require:
      - service: protondrive_daemon
      - cmd: protondrive_reload
