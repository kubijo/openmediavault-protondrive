{% set email_config = salt['omv_conf.get']('conf.system.notification.email') %}
{% set notifications = salt['omv_conf.get']('conf.system.notification.notification') %}

configure_monit_protondrive:
  file.managed:
    - name: /etc/monit/conf.d/openmediavault-protondrive.conf
    - source: salt://omv/deploy/protondrive/files/monit.conf.j2
    - template: jinja
    - context:
        email_config: {{ email_config | json }}
        notifications: {{ notifications | json }}
    - user: root
    - group: root
    - mode: '0600'

# Package maintenance suspends this group after successful container recovery.
# Re-enable it after OMV has validated and reloaded the generated configuration.
monitor_protondrive_services:
  cmd.run:
    - name: /usr/sbin/omv-protondrive monitor
    - require:
      - service: reload_monit_service
