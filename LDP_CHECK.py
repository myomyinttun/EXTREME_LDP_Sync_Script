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
BACKUP_VLAN = "CR77-DR102_955"

PEER_OSPF_IP = "172.17.79.46"
VPLS_PEER = "172.17.80.102"
VPLS_SERVICE = "YGN_SG_CE_Uplink_VPLS"

COOLDOWN_SEC = 300
COOLDOWN_FILE = "/tmp/ldp_recovery.lock"

SYSLOG_TAG = "CHECK-PRIMARY-LDP"
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
def primary_ldp_adj(vlan):                                       
    try:                                     
        out = clicmd("show mpls ldp interface", capture=True)
        for line in out.splitlines():                  
            if vlan in line:                            
                parts = line.split()                             
                if len(parts) > 1:           
                    try:                                     
                        return int(parts[1])           
                    except:                             
                        return 0                                 
    except:                                  
        pass                                                 
    return 0                                           
                                                        
# ------------------------                                       
# VPLS next-hop + Peer IP check                              
# ------------------------                                   
def vpls_nexthop_wrong(expected_peer_ip, expected_next_hop):
    try:                                                
        out = clicmd("show vpls %s detail" % VPLS_SERVICE, capture=True)
    except:                                                  
        return True                                          
                                                            
    peer_found = False                                  
    next_hop_ok = False                                                 
                                                             
    for line in out.splitlines():                            
        line = line.strip()                                 
        if line.startswith("Peer IP:"):                                 
            if expected_peer_ip in line:                                
                peer_found = True            
                                                             
        if line.startswith("Next Hop Addr"):                
            if expected_next_hop in line:                               
                next_hop_ok = True                                      
                                             
    return not peer_found or not next_hop_ok                 
                                                            
# ------------------------                              
# TOGGLE BACKUP VLAN                                                    
# ------------------------                                   
def toggle_backup_vlan():                                    
    if DRY_RUN:                                             
        log("DRY-RUN: Would toggle VLAN %s" % BACKUP_VLAN)
        return                                                          
                                                             
    try:                                                     
        log("Disabling MPLS LDP on VLAN %s" % BACKUP_VLAN)  
        clicmd("disable mpls ldp vlan %s" % BACKUP_VLAN)                
                                                                        
        log("Waiting 60 seconds...")         
        time.sleep(60)                                       
                                                            
        log("Enabling MPLS LDP on VLAN %s" % BACKUP_VLAN)               
        clicmd("enable mpls ldp vlan %s" % BACKUP_VLAN)                 
                                             
        log("Backup VLAN toggle completed")                  
    except Exception as e:                                  
        log("ERROR: Failed to toggle backup VLAN: %s" % str(e))         
                                                                        
# ------------------------                                   
# MAIN LOGIC                                                 
# ------------------------                                  
def main():                                                    
    log("========== CHECK STARTING ==========")                         
                                                             
    # CHECK 1: OSPF                                          
    if not ospf_neighbor_is_full(PEER_OSPF_IP):             
        log("OSPF NOT FULL - NO ACTION")                       
        return                                                          
    log("OSPF is FULL - OK")                                 
                                                             
    # CHECK 2: LDP                                          
    adj = primary_ldp_adj(PRIMARY_VLAN)                        
    if adj <= 0:                                                        
        log("LDP DOWN (adj: %d) - NO ACTION" % adj)          
        return                                               
    log("LDP is UP (adj: %d) - OK" % adj)                   
                                                                        
    # CHECK 3: VPLS                                                     
    if vpls_nexthop_wrong(VPLS_PEER, PEER_OSPF_IP):
        log("VPLS NEXT-HOP is WRONG")                        
                                                            
        if cooldown_active():                                           
            log("Cooldown active - SKIPPING")                           
            return                                 
                                                             
        log("*** TRIGGERING BACKUP VLAN TOGGLE ***")        
        toggle_backup_vlan()                                            
        set_cooldown()                                                  
        log("========== ACTION COMPLETED ==========")
    else:                                                    
        log("VPLS NEXT-HOP is CORRECT - NO ACTION NEEDED")  
        log("========== ALL OK ==========")                             

# ------------------------                                  
# ENTRY POINT                                                           
# ------------------------                                              
if __name__ == "__main__":                           
    try:                                                     
        main()                                              
    except Exception as e:                                     
        log("FATAL ERROR: %s" % str(e))                                 
        raise                                        
                                                             
                                                                  