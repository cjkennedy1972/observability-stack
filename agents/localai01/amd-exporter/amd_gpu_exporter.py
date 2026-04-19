#!/usr/bin/env python3
"""Lightweight AMD GPU Prometheus exporter using rocm-smi JSON output."""
import json, subprocess, os
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = int(os.environ.get('PORT', 2021))
ROCM_SMI = os.environ.get('ROCM_SMI', '/opt/rocm/bin/rocm-smi')

# Map: prometheus_metric -> (help text, json_key, optional multiplier)
METRICS = [
    ('amd_smi_temperature_edge_celsius',   'GPU edge temperature Celsius',       'Temperature (Sensor edge) (C)',            1.0),
    ('amd_smi_temperature_junction_celsius','GPU junction temperature Celsius',   'Temperature (Sensor junction) (C)',        1.0),
    ('amd_smi_temperature_mem_celsius',    'GPU memory temperature Celsius',      'Temperature (Sensor memory) (C)',          1.0),
    ('amd_smi_gpu_busy_percent',           'GPU utilization percent',             'GPU use (%)',                              1.0),
    ('amd_smi_memory_busy_percent',        'GPU memory VRAM used percent',        'GPU Memory Allocated (VRAM%)',             1.0),
    ('amd_smi_power_watts',                'GPU average socket power watts',      'Average Graphics Package Power (W)',       1.0),
    ('amd_smi_max_power_watts',            'GPU max package power watts',         'Max Graphics Package Power (W)',           1.0),
    ('amd_smi_fan_speed_percent',          'GPU fan speed percent',               'Fan speed (%)',                            1.0),
    ('amd_smi_fan_rpm',                    'GPU fan speed RPM',                   'Fan RPM',                                  1.0),
    ('amd_smi_voltage_mv',                 'GPU voltage millivolts',              'Voltage (mV)',                             1.0),
    ('amd_smi_avg_gfxclk_mhz',            'GPU average GFX clock MHz',           'average_gfxclk_frequency (MHz)',           1.0),
    ('amd_smi_avg_uclk_mhz',              'GPU average memory clock MHz',        'average_uclk_frequency (MHz)',             1.0),
    ('amd_smi_avg_socket_power_watts',     'GPU average socket power watts',      'average_socket_power (W)',                 1.0),
    ('amd_smi_avg_gfx_activity_percent',   'GPU average GFX activity percent',    'average_gfx_activity (%)',                 1.0),
]


def safe_float(val):
    """Extract float from a value that may be a string with units."""
    if val is None or val == 'N/A':
        return None
    s = str(val).strip()
    # Handle parenthesized MHz like "(3415Mhz)"
    s = s.strip('()').replace('Mhz', '').replace('MHz', '').replace('W', '').strip()
    try:
        return float(s)
    except ValueError:
        return None


def collect():
    try:
        env = dict(os.environ)
        env.setdefault('LD_LIBRARY_PATH', '/opt/rocm/lib:/opt/rocm/lib64')
        result = subprocess.run(
            [ROCM_SMI, '--showallinfo', '--json'],
            capture_output=True, text=True, timeout=15, env=env
        )
        raw = result.stdout.strip()
        if not raw:
            raw = result.stderr.strip()
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return f'# HELP amd_smi_up GPU exporter up\n# TYPE amd_smi_up gauge\namd_smi_up 0\n# ERROR: {e}\n'
    except Exception as e:
        return f'# HELP amd_smi_up GPU exporter up\n# TYPE amd_smi_up gauge\namd_smi_up 0\n# ERROR: {e}\n'

    lines = ['# HELP amd_smi_up GPU exporter up', '# TYPE amd_smi_up gauge', 'amd_smi_up 1']

    for metric, help_text, _, _ in METRICS:
        lines.append(f'# HELP {metric} {help_text}')
        lines.append(f'# TYPE {metric} gauge')

    for gpu_key, gpu_data in data.items():
        if not gpu_key.startswith('card') or not isinstance(gpu_data, dict):
            continue
        gpu_id = gpu_key.replace('card', '')
        device_name = gpu_data.get('Device Name', 'unknown').replace('"', '').replace(' ', '_')
        labels = f'gpu_id="{gpu_id}",device="{device_name}"'

        for metric, _, json_key, multiplier in METRICS:
            val = safe_float(gpu_data.get(json_key))
            if val is not None:
                lines.append(f'{metric}{{{labels}}} {val * multiplier}')

    lines.append('')
    return '\n'.join(lines)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/metrics':
            body = collect().encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ('/', '/health'):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'ok\n')
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == '__main__':
    print(f'AMD GPU exporter listening on :{PORT}/metrics  (rocm-smi: {ROCM_SMI})', flush=True)
    HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
