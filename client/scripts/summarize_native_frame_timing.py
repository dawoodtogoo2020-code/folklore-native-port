"""Summarize only adjacent successful presents within marked host-clock windows.

Accepts the legacy QPC schema (CSV present_begin_qpc,present_end_qpc,qpc_frequency;
marker qpc_frequency, start_qpc/end_qpc/qpc_seconds) and the generic schema (CSV
present_begin_ticks,present_end_ticks,clock_frequency; marker clock_frequency,
start_ticks/end_ticks/seconds). Ticks come from whatever host clock the probe used
(QPC on Windows, CLOCK_MONOTONIC nanoseconds on Linux)."""
import argparse,csv,hashlib,json,math,re,statistics,sys
from pathlib import Path

CSV_SCHEMAS={'legacy_qpc':('present_begin_qpc','present_end_qpc','qpc_frequency'),'generic':('present_begin_ticks','present_end_ticks','clock_frequency')}
MIN_WINDOW_INTERVALS=100

class TimingError(ValueError):
    pass

def require(condition,message):
    if not condition: raise TimingError(message)

def is_int(value):
    return isinstance(value,int) and not isinstance(value,bool)

def csv_int(value,what):
    require(isinstance(value,str) and re.fullmatch(r'[0-9]+',value) is not None,f'{what}: expected non-negative integer, got {value!r}')
    return int(value)

def stats(values):
    require(values and all(math.isfinite(v) and v>0 for v in values),'statistics need finite positive samples')
    s=sorted(values);n=len(s)
    percentile=lambda q:s[math.ceil(q*n)-1]
    result=dict(samples=n,mean_ms=statistics.mean(s),median_ms=statistics.median(s),p95_ms=percentile(.95),p99_ms=percentile(.99),min_ms=s[0],max_ms=s[-1])
    require(all(math.isfinite(v) for v in result.values()),'non-finite statistic')
    return result

def load_rows(csv_path):
    """Return (schema, frequency, [(present_number, begin, end), ...]) after validation."""
    with Path(csv_path).open(newline='') as source:
        reader=csv.DictReader(source)
        fields=tuple(reader.fieldnames or ())
        schema=next((name for name,cols in CSV_SCHEMAS.items() if fields==('present_number',)+cols),None)
        require(schema is not None,f'unrecognized frame timing CSV header: {fields}')
        begin_key,end_key,frequency_key=CSV_SCHEMAS[schema]
        rows=[];frequencies=set()
        for line,row in enumerate(reader,start=2):
            require(None not in row and all(v is not None for v in row.values()),f'line {line}: wrong column count')
            rows.append((csv_int(row['present_number'],f'line {line} present_number'),csv_int(row[begin_key],f'line {line} {begin_key}'),csv_int(row[end_key],f'line {line} {end_key}')))
            frequencies.add(csv_int(row[frequency_key],f'line {line} {frequency_key}'))
    require(rows,'frame timing CSV has no rows')
    require(len(frequencies)==1,f'expected one clock frequency, got {sorted(frequencies)}')
    frequency=frequencies.pop();require(frequency>0,f'clock frequency must be positive, got {frequency}')
    for number,begin,end in rows:
        require(end>=begin,f'present {number}: end before begin')
    for prev,row in zip(rows,rows[1:]):
        require(row[0]==prev[0]+1,f'present numbers not adjacent: {prev[0]} -> {row[0]}')
        require(row[1]>prev[1],f'present {row[0]}: begin timestamp not strictly increasing')
        require(row[2]>prev[2],f'present {row[0]}: end timestamp not strictly increasing')
        require(row[1]>=prev[2],f'present {row[0]}: overlaps previous present call')
    return schema,frequency,rows

def pick(mapping,generic,legacy,what):
    present=[mapping[k] for k in (generic,legacy) if k in mapping]
    require(present,f'{what}: missing {generic} (or legacy {legacy})')
    require(all(v==present[0] for v in present),f'{what}: {generic} and {legacy} disagree')
    return present[0]

def marker_frequency(marker):
    calibration=marker.get('clock_calibration')
    require(isinstance(calibration,dict),'marker clock_calibration missing')
    frequency=pick(calibration,'clock_frequency','qpc_frequency','clock_calibration')
    require(is_int(frequency) and frequency>0,f'marker clock frequency must be a positive integer, got {frequency!r}')
    return frequency

def marker_windows(marker):
    windows=marker.get('windows')
    require(isinstance(windows,list) and windows,'marker has no windows')
    parsed=[];previous_end=None
    for w in windows:
        require(isinstance(w,dict) and 'index' in w,'window missing index')
        what=f"window {w['index']}"
        start=pick(w,'start_ticks','start_qpc',what);end=pick(w,'end_ticks','end_qpc',what);seconds=pick(w,'seconds','qpc_seconds',what)
        require(is_int(start) and is_int(end) and 0<=start<end,f'{what}: invalid tick bounds')
        require(isinstance(seconds,(int,float)) and not isinstance(seconds,bool) and math.isfinite(seconds) and seconds>0,f'{what}: invalid seconds')
        require(previous_end is None or start>=previous_end,f'{what}: overlaps previous window')
        require('memory_before' in w and 'memory_after' in w,f'{what}: missing memory fields')
        parsed.append((w,start,end,seconds));previous_end=end
    return parsed

def summarize(run,min_intervals=MIN_WINDOW_INTERVALS):
    run=Path(run)
    marker=json.loads((run/'timing-windows.json').read_text())
    schema,f,rows=load_rows(run/'frame-timing.csv')
    require(f==marker_frequency(marker),'CSV clock frequency does not match marker calibration')
    windows=[];all_intervals=[];all_calls=[]
    for w,start,end,seconds in marker_windows(marker):
        require(math.isclose(seconds,(end-start)/f,rel_tol=1e-6,abs_tol=1e-9),f"window {w['index']}: seconds disagree with tick bounds")
        require(rows[0][2]<=start and rows[-1][2]>=end,f"window {w['index']}: CSV does not cover complete measurement window")
        intervals=[];calls=[]
        for prev,row in zip(rows,rows[1:]):
            if start<=prev[2]<row[2]<=end:
                intervals.append((row[2]-prev[2])*1000/f)
                calls.append((row[2]-row[1])*1000/f)
        require(len(intervals)>=min_intervals,f"window {w['index']}: {len(intervals)} intervals, need at least {min_intervals}")
        windows.append(dict(index=w['index'],seconds=seconds,completion_intervals=stats(intervals),present_call_cpu_duration=stats(calls),memory_before=w['memory_before'],memory_after=w['memory_after']))
        all_intervals.extend(intervals);all_calls.extend(calls)
    slowest=sorted(all_intervals,reverse=True)[:math.ceil(.01*len(all_intervals))]
    average_rate=1000/statistics.mean(all_intervals);one_percent_low=1000/statistics.mean(slowest)
    require(math.isfinite(average_rate) and math.isfinite(one_percent_low),'non-finite rate result')
    result=dict(run=str(run),binary_sha256=marker['binary_sha256'],clock_calibration=marker['clock_calibration'],clock_frequency=f,csv_schema=schema,warmup_seconds=marker['warmup_seconds'],windows=windows,pooled_completion_intervals=stats(all_intervals),pooled_present_call_cpu_duration=stats(all_calls),average_successful_present_rate_hz=average_rate,one_percent_low_hz=one_percent_low,one_percent_low_sample_count=len(slowest),definitions={'clock':'Host monotonic clock ticks recorded by the probe (QPC on Windows, CLOCK_MONOTONIC nanoseconds on Linux), converted with the single CSV clock frequency.','completion_interval':'Difference between consecutive successful present return timestamps; both endpoints inside a measurement window. Host cadence including limiter, FIFO/render work, scheduling, waits and diagnostics. Not GPU time, displayed scanout cadence or proof of displayed FPS.','present_call_cpu_duration':'Host-clock elapsed wall time around backend present() call; may include GPU waits and readback, not isolated CPU execution or GPU execution.','percentiles':'Nearest-rank ceil(p*n), no interpolation. Median uses conventional midpoint average.','one_percent_low':'1000 divided by arithmetic mean of the slowest ceil(1%*n) completion intervals in milliseconds; windows pooled after excluding crossing-boundary intervals.','average_rate':'1000 / mean completion interval in milliseconds; successful-present rate on the host, not displayed frame rate.'},raw_csv_sha256=hashlib.sha256((run/'frame-timing.csv').read_bytes()).hexdigest(),loading_time=None,gpu_execution_time=None)
    (run/'frame-timing-summary.json').write_text(json.dumps(result,indent=2,allow_nan=False));return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run',type=Path);a=p.parse_args()
    try: r=summarize(a.run)
    except (TimingError,KeyError) as error: sys.exit(f'invalid frame timing data: {error}')
    print(json.dumps({k:v for k,v in r.items() if k in ['run','pooled_completion_intervals','pooled_present_call_cpu_duration','average_successful_present_rate_hz','one_percent_low_hz']}))
