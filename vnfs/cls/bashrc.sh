alias logs='tail -n 30 -F /var/log/cls.log'
alias clsmode='python3 /app/vnf.py mode'   # clsmode [rules|ml|collect]: show or switch live
if [ -t 1 ] && [ -z "$CLS_LOG_SHOWN" ]; then
    export CLS_LOG_SHOWN=1
    echo "=== vnf-cls live log (Ctrl+C for a shell, type 'logs' to resume) ==="
    tail -n 30 -F /var/log/cls.log
fi
