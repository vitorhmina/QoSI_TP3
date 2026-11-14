alias logs='tail -n 30 -F /var/log/mon.log'
if [ -t 1 ] && [ -z "$MON_LOG_SHOWN" ]; then
    export MON_LOG_SHOWN=1
    echo "=== vnf-mon live log (Ctrl+C for a shell, type 'logs' to resume) ==="
    tail -n 30 -F /var/log/mon.log
fi
