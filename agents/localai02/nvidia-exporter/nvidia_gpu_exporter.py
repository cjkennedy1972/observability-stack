#!/usr/bin/env python3
"""Lightweight NVIDIA GPU Prometheus exporter using nvidia-smi."""
import subprocess, os
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = int(os.environ.get('PORT', 9835))
NVIDIA_SMI = os.environ.get('NVIDIA_SMI', 'nvidia-smi')

QUERY_FIELDS = [
    ('index',                  None,                               None),
    ('name',                   None,                               None),
    ('driver_version',         None,                               None),
    ('temperature.gpu',        'nvidia_smi_temperature_gpu',       'GPU temperature Celsius'),
    ('utilization.gpu',        'nvidia_smi_utilization_gpu_ratio', 'GPU utilization ratio'),
    ('utilization.memory',     'nvidia_smi_utilization_memory_ratio', 'Memory utilization ratio'),
    ('memory.total',           'nvidia_smi_memory_total_bytes',    'GPU memory total bytes'),
    ('memory.used',            'nvidia_smi_memory_used_bytes',     'GPU memory used bytes'),
    ('memory.free',            'nvidia_smi_memory_free_bytes',     'GPU memory free bytes'),
    ('power.draw',             'nvidia_smi_power_draw_watts',      'GPU power draw watts'),
    ('power.limit',            'nvidia_smi_power_limit_watts',     'GPU power limit watts'),
    ('clocks.current.graphics','nvidia_smi_clock_graphics_mhz',   'GPU graphics clock MHz'),
    ('clocks.current.memory',  'nvidia_smi_clock_memory_mhz',     'GPU memory clock MHz'),
    ('fan.speed',              'nvidia_smi_fan_speed_percent',     'GPU fan speed percent'),
]

QUERY_KEYS   = [f[0] for f in QUERY_FIELDS]
METRIC_MAP   = {f[0]: (f[1], f[2]) for f in QUERY_FIELDS if f[1]}

# nvidia-smi reports memory in MiB, convert to bytes
MIB_FIELDS = {'memory.total', 'memory.used', 'memory.free'}
# utilization fields come as "NN %" — strip to ratio
PCT_FIELDS = {'utilization.gpu', 'utilization.memory', 'fan.speed'}


def collect():
    try:
        result = subprocess.run(
            [NVIDIA_SMI,
             '--query-gpu=' + ','.join(QUERY_KEYS),
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        rows = [r.strip() for r in result.stdout.strip().splitlines()]
    except Exception as e:
        return f'# HELP nvidia_smi_up GPU exporter up\n# TYPE nvidia_smi_up gauge\nnvidia_smi_up 0\n# ERROR: {e}\n'

    lines = ['# HELP nvidia_smi_up GPU exporter up', '# TYPE nvidia_smi_up gauge', 'nvidia_smi_up 1']

    declared = set()
    gpu_rows = []
    for row in rows:
        vals = [v.strip() for v in row.split(',')]
        if len(vals) < len(QUERY_KEYS):
            continue
        data = dict(zip(QUERY_KEYS, vals))
        gpu_rows.append(data)

    for metric, (prom_name, help_text) in METRIC_MAP.items():
        if prom_name not in declared:
            lines.append(f'# HELP {prom_name} {help_text}')
            lines.append(f'# TYPE {prom_name} gauge')
            declared.add(prom_name)

    for data in gpu_rows:
        idx    = data.get('index', '0')
        name   = data.get('name', 'unknown').replace(' ', '_').replace(',', '')
        driver = data.get('driver_version', 'unknown')
        labels = f'gpu_index="{idx}",gpu="{name}",driver="{driver}"'

        for field, (prom_name, _) in METRIC_MAP.items():
            raw = data.get(field, '').strip()
            if raw in ('N/A', '[Not Supported]', ''):
                continue
            try:
                val = float(raw)
                if field in MIB_FIELDS:
                    val *= 1024 * 1024
                elif field in PCT_FIELDS:
                    val /= 100.0
                lines.append(f'{prom_name}{{{labels}}} {val}')
            except ValueError:
                pass

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
    print(f'NVIDIA GPU exporter listening on :{PORT}/metrics  (nvidia-smi: {NVIDIA_SMI})', flush=True)
    HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
