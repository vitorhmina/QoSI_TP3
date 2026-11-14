# Show the live firewall log when a console is opened (Ctrl+C gives you a normal shell)
alias logs='tail -n 30 -F /var/log/fw.log'
if [ -t 1 ] && [ -z "$FW_LOG_SHOWN" ]; then
    export FW_LOG_SHOWN=1
    echo "=== vnf-fw live log (Ctrl+C for a shell, type 'logs' to resume) ==="
    tail -n 30 -F /var/log/fw.log
fi