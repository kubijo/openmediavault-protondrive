{% if config.enable -%}
probe:
  test.nop: []
{% else -%}
probe:
  test.nop: []
{% endif -%}
