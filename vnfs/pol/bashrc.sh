alias logs='tail -n 30 -F /var/log/pol.log'
if [ -t 1 ] && [ -z "$POL_LOG_SHOWN" ]; then
    export POL_LOG_SHOWN=1
    echo "=== vnf-pol live log (Ctrl+C for a shell, type 'logs' to resume) ==="
    tail -n 30 -F /var/log/pol.log
fi
