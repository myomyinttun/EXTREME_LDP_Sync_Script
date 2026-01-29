# coding: utf-8
#!/usr/bin/env python
# EXOS Python 2.7 compatible
from exsh import clicmd
import time
import os

# ------------------------
# CONFIG
# ------------------------
PRIMARY_VLAN = "CR77-DR102_595"
PRIMARY_VLAN_IP = "172.17.79.46"

SECONDARY_VLAN = "CR77-DR102_755"
SECONDARY_VLAN_IP = "172.17.79.47"

BACKUP_VLAN = "CR77-DR102_955"
BACKUP_VLAN_IP = "172.17.79.48"

VPLS_PEER = "172.17.80.102"
VPLS_SERVICE = "YGN_SG_CE_Uplink_VPLS"

COOLDOWN_SEC = 300
COOLDOWN_FILE = "/tmp/ldp_recovery.lock"

SYSLOG_TAG = "CHECK-VLAN-LDP"
DRY_RUN = False

# ------------------------
# SIMPLE LOGGING
# ------------------------
def log(msg):
    try:
        clicmd('create log message "%s: %s"' % (SYSLOG_TAG, msg))
    except:
        pass

# ------------------------
# COOLDOWN
# ------------------------ 
def cooldown_active():
    if not os.path.exists(COOLDOWN_FILE):
        return False
    try:
        with open(COOLDOWN_FILE, "r") as f:
            last = int(f.read().strip())
        return (int(time.time()) - last) < COOLDOWN_SEC
    except:
        return False

def set_cooldown():
    try:
        with open(COOLDOWN_FILE, "w") as f:
            f.write(str(int(time.time())))
    except:
        pass

# ------------------------
# OSPF CHECK
# ------------------------
def ospf_neighbor_is_full(ip):
    try:
        out = clicmd("show ospf neighbor", capture=True)
        for line in out.splitlines():
            if ip in line and "FULL" in line:
                return True
    except:
        pass
    return False

# ------------------------
# LDP CHECK
# ------------------------
def ldp_adjacency_up(vlan):
    try:
        out = clicmd("show mpls ldp interface", capture=True)
        for line in out.splitlines():
            if vlan in line:
                parts = line.split()
                if len(parts) > 1:
                    try:
                        adj_count = int(parts[1])
                        return adj_count > 0
                    except:
                        return False
    except:
        pass
    return False

# ------------------------
# VPLS next-hop check
# ------------------------
def get_vpls_nexthop():
    try:
        out = clicmd("show vpls %s detail" % VPLS_SERVICE, capture=True)
    except:
        return None

    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Next Hop Addr"):
            parts = line.split(":")
            if len(parts) > 1:
                return parts[1].strip()

    return None

# ------------------------
# TOGGLE VLAN
# ------------------------
def toggle_vlan(vlan_name):
    if DRY_RUN:
        log("DRY-RUN: Would toggle VLAN %s" % vlan_name)
        return

    try:
        log("Disabling MPLS LDP on VLAN %s" % vlan_name)
        clicmd("disable mpls ldp vlan %s" % vlan_name)

        log("Waiting 60 seconds...")
        time.sleep(60)

        log("Enabling MPLS LDP on VLAN %s" % vlan_name)
        clicmd("enable mpls ldp vlan %s" % vlan_name)

        log("VLAN %s toggle completed" % vlan_name)
    except Exception as e:
        log("ERROR: Failed to toggle VLAN %s: %s" % (vlan_name, str(e)))

# ------------------------
# MAIN LOGIC
# ------------------------
def main():
    log("========== CHECK STARTING ==========")

    # Get current VPLS next-hop
    vpls_nexthop = get_vpls_nexthop()
    if vpls_nexthop is None:
        log("ERROR: Cannot determine VPLS next-hop")
        return

    log("Current VPLS next-hop: %s" % vpls_nexthop)

    # Check PRIMARY VLAN status
    log("Checking PRIMARY VLAN (%s - %s)..." % (PRIMARY_VLAN, PRIMARY_VLAN_IP))
    primary_ospf = ospf_neighbor_is_full(PRIMARY_VLAN_IP)
    primary_ldp = ldp_adjacency_up(PRIMARY_VLAN)

    log("  PRIMARY: OSPF=%s, LDP=%s" % (primary_ospf, primary_ldp))

    # Check SECONDARY VLAN status
    log("Checking SECONDARY VLAN (%s - %s)..." % (SECONDARY_VLAN, SECONDARY_VLAN_IP))
    secondary_ospf = ospf_neighbor_is_full(SECONDARY_VLAN_IP)
    secondary_ldp = ldp_adjacency_up(SECONDARY_VLAN)

    log("  SECONDARY: OSPF=%s, LDP=%s" % (secondary_ospf, secondary_ldp))

    # ============================================================
    # DECISION LOGIC
    # ============================================================

    # CASE 1: PRIMARY is UP (OSPF + LDP)
    if primary_ospf and primary_ldp:
        if vpls_nexthop == PRIMARY_VLAN_IP:
            log("PRIMARY VLAN: OSPF UP + LDP UP + Next-hop CORRECT")
            log("========== ALL OK ==========")
            return
        else:
            log("PRIMARY VLAN: OSPF UP + LDP UP but Next-hop WRONG")
            log("Expected: %s, Got: %s" % (PRIMARY_VLAN_IP, vpls_nexthop))

            if cooldown_active():
                log("Cooldown active - SKIPPING action")
                return

            log("*** ACTION: Toggle SECONDARY and BACKUP VLANs ***")
            toggle_vlan(SECONDARY_VLAN)
            toggle_vlan(BACKUP_VLAN)
            set_cooldown()
            log("========== ACTION COMPLETED ==========")
            return

    # CASE 2: PRIMARY is DOWN, but SECONDARY is UP (OSPF + LDP)
    if secondary_ospf and secondary_ldp:
        if vpls_nexthop == SECONDARY_VLAN_IP:
            log("SECONDARY VLAN: OSPF UP + LDP UP + Next-hop CORRECT")
            log("(PRIMARY is down, using SECONDARY)")
            log("========== ALL OK ==========")
            return
        else:
            log("SECONDARY VLAN: OSPF UP + LDP UP but Next-hop WRONG")
            log("Expected: %s, Got: %s" % (SECONDARY_VLAN_IP, vpls_nexthop))

            if cooldown_active():
                log("Cooldown active - SKIPPING action")
                return

            log("*** ACTION: Toggle BACKUP VLAN only ***")
            toggle_vlan(BACKUP_VLAN)
            set_cooldown()
            log("========== ACTION COMPLETED ==========")
            return

    # CASE 3: BOTH PRIMARY and SECONDARY are DOWN
    log("*** WARNING: Both PRIMARY and SECONDARY VLANs are DOWN ***")
    log("PRIMARY: OSPF=%s, LDP=%s" % (primary_ospf, primary_ldp))
    log("SECONDARY: OSPF=%s, LDP=%s" % (secondary_ospf, secondary_ldp))
    log("Using BACKUP VLAN (%s)" % BACKUP_VLAN)
    
    # Check if backup is working
    if vpls_nexthop == BACKUP_VLAN_IP:
        log("BACKUP VLAN next-hop is CORRECT (%s)" % BACKUP_VLAN_IP)
        log("========== FAILOVER TO BACKUP ==========")
    else:
        log("WARNING: BACKUP VLAN next-hop may be incorrect")
        log("Expected: %s, Got: %s" % (BACKUP_VLAN_IP, vpls_nexthop))
        log("========== ISSUE LOGGED ==========")

# ------------------------
# ENTRY POINT
# ------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("FATAL ERROR: %s" % str(e))
        raise