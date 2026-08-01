#!/usr/bin/env python3
# author: murayefeskamus

import a2s
import threading
import time
import os
import sys
import json

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

SERVER_LIST  = "servers.txt"
HISTORY_FILE = "history.json"
FAVORITES_FILE = "favorites.json"
TIMEOUT      = 2.0
MAX_WORKERS  = 256
RETRY        = 3

if os.name == "nt":
    os.system("color")

R   = "\033[91m"
G   = "\033[92m"
Y   = "\033[93m"
C   = "\033[96m"
W   = "\033[97m"
DIM = "\033[2m"
RST = "\033[0m"
B   = "\033[1m"
MG  = "\033[95m"

def clear():
    os.system("cls" if os.name == "nt" else "clear")


def copy_to_clipboard(text):
    try:
        import subprocess
        if os.name == "nt":
            subprocess.run("clip", input=text.encode(), check=True, shell=True)
        else:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
        return True
    except Exception:
        try:
            import tkinter as tk
            r = tk.Tk()
            r.withdraw()
            r.clipboard_clear()
            r.clipboard_append(text)
            r.update()
            r.after(100, r.destroy)
            r.mainloop()
            return True
        except Exception:
            return False


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def add_history(entry):
    history = load_json(HISTORY_FILE, [])
    history.insert(0, entry)
    history = history[:500]  # max 500 kayıt
    save_json(HISTORY_FILE, history)

def load_favorites():
    return load_json(FAVORITES_FILE, [])

def save_favorites(favs):
    save_json(FAVORITES_FILE, favs)


def load_servers(path):
    servers = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or ":" not in line:
                    continue
                ip, port = line.rsplit(":", 1)
                servers.append((ip.strip(), int(port.strip())))
    except FileNotFoundError:
        print(f"{R}[!] {path} not found!{RST}")
        sys.exit(1)
    return servers


def query_server(addr, search):
    search_lower = search.lower()

    info = None
    for _ in range(RETRY):
        try:
            info = a2s.info(addr, timeout=TIMEOUT)
            break
        except Exception:
            time.sleep(0.05)
    if info is None or info.player_count == 0:
        return None

    # Rate limit önlemi — INFO'dan hemen sonra PLAYER atmak
    # bazı sunucularda drop'a sebep oluyor
    time.sleep(0.1)

    players = None
    for attempt in range(RETRY):
        try:
            players = a2s.players(addr, timeout=TIMEOUT)
            if players is not None:
                break
        except Exception:
            # Her retry'da biraz daha bekle
            time.sleep(0.2 * (attempt + 1))

    if players is None:
        # Son çare: INFO'yu tekrar al, player sayısı hala doluysa
        # farklı timeout ile bir kez daha dene
        try:
            info2 = a2s.info(addr, timeout=TIMEOUT + 1.0)
            if info2 and info2.player_count > 0:
                players = a2s.players(addr, timeout=TIMEOUT + 1.0)
        except Exception:
            pass

    if players is None:
        return None

    for p in players:
        name = p.name.strip()
        if not name:
            continue
        if search_lower in name.lower():
            return {
                "ip"         : addr[0],
                "port"       : addr[1],
                "server"     : info.server_name[:60],
                "map"        : info.map_name,
                "player_count": info.player_count,
                "max_players" : info.max_players,
                "players"    : f"{info.player_count}/{info.max_players}",
                "matched"    : name,
                "score"      : p.score,
                "time"       : int(p.duration),
                "all"        : [pl.name for pl in players if pl.name.strip()],
                "found_at"   : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
    return None

def query_server_info_only(addr):
    """İki aşamalı tarama için sadece INFO döner."""
    for _ in range(RETRY):
        try:
            info = a2s.info(addr, timeout=TIMEOUT)
            if info.player_count > 0:
                return addr, info
            return None
        except Exception:
            pass
    return None


def format_time(seconds):
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    return f"{m}m {s}s"

def player_bar(count, max_p):
    pct = count / max_p if max_p else 0
    if pct >= 0.9:
        col = R
    elif pct >= 0.5:
        col = Y
    else:
        col = G
    filled = int(pct * 10)
    bar = "■" * filled + "□" * (10 - filled)
    return f"{col}{bar}{RST} {count}/{max_p}"

def print_header():
    print(f"""
  {C}{B}L4D2  PLAYER  FINDER{RST}
  {DIM}author: murayefeskamus{RST}
""")

def print_help():
    print(f"""
  {B}Commands:{RST}
    {C}<name>{RST}          Search for a player by name (partial match)
    {C}multi{RST}           Search for multiple players at once
    {C}favs{RST}            List saved favorite players
    {C}addfav <name>{RST}   Add a player to favorites
    {C}delfav <name>{RST}   Remove a player from favorites
    {C}scanfavs{RST}        Scan all favorites at once
    {C}track <name>{RST}    Track a player (auto-scan every 60s)
    {C}history{RST}         Show recent search history
    {C}help{RST}            Show this help
    {C}exit{RST}            Quit
""")


def run_search(query, servers, silent=False):
    """
    İki aşamalı tarama:
    1. Tüm sunuculara INFO at, dolu olanları bul
    2. Sadece dolu sunuculara PLAYER at
    """
    if not silent:
        print(f"\n{Y}[*] Searching: {B}{query}{RST}{Y}  ({len(servers)} servers){RST}")
        print(f"    {DIM}Phase 1: scanning for active servers...{RST}")

    # Faz 1 — INFO
    active = []
    counter = [0]
    lock = threading.Lock()
    total = len(servers)
    start = time.time()
    done = [False]

    def progress(phase):
        while not done[0]:
            with lock:
                c = counter[0]
            elapsed = time.time() - start
            pct = int(c / total * 38)
            bar = "█" * pct + "░" * (38 - pct)
            print(f"\r    [{bar}] {c}/{total}  {elapsed:.1f}s  {DIM}phase {phase}{RST}", end="", flush=True)
            time.sleep(0.1)

    pt = threading.Thread(target=progress, args=(1,), daemon=True)
    pt.start()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(query_server_info_only, addr): addr for addr in servers}
        for fut in as_completed(futures):
            result = fut.result()
            with lock:
                counter[0] += 1
                if result:
                    active.append(result[0])

    done[0] = True
    pt.join(timeout=0.3)
    print(f"\r{' '*70}\r", end="")

    if not silent:
        print(f"    {DIM}Phase 1 done: {len(active)} active servers found{RST}")
        print(f"    {DIM}Phase 2: querying player lists...{RST}")

    if not active:
        if not silent:
            print(f"{R}[!] No active servers found.{RST}\n")
        return []

    # Faz 2 — PLAYER
    found = []
    counter[0] = 0
    total = len(active)
    done[0] = False

    pt2 = threading.Thread(target=progress, args=(2,), daemon=True)
    pt2.start()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(query_server, addr, query): addr for addr in active}
        for fut in as_completed(futures):
            result = fut.result()
            with lock:
                counter[0] += 1
                if result:
                    found.append(result)

    done[0] = True
    pt2.join(timeout=0.3)
    elapsed = time.time() - start
    print(f"\r{' '*70}\r", end="")

    if not silent:
        print(f"{DIM}    Done in {elapsed:.1f}s{RST}\n")

    return found

def print_results(found, query, save=True):
    if not found:
        print(f"{R}[!] '{query}' not found on any server.{RST}\n")
        return

    print(f"{G}{B}[+] {len(found)} match(es) found:{RST}\n")

    for i, r in enumerate(found, 1):
        connect = f"connect {r['ip']}:{r['port']}"
        others  = [n for n in r["all"] if n.lower() != r["matched"].lower()]
        print(f"  {C}{B}#{i}  {W}{B}{r['matched']}{RST}")
        print(f"      {DIM}Server  :{RST} {r['server']}")
        print(f"      {DIM}Address :{RST} {Y}{connect}{RST}")
        print(f"      {DIM}Map     :{RST} {r['map']}")
        print(f"      {DIM}Players :{RST} {player_bar(r['player_count'], r['max_players'])}  {DIM}Score:{RST} {r['score']}  {DIM}Time:{RST} {format_time(r['time'])}")
        if others:
            print(f"      {DIM}In lobby:{RST} {', '.join(others)}")
        print()

        # Panoya kopyala (ilk sonuç)
        if i == 1:
            if copy_to_clipboard(connect):
                print(f"      {DIM}✓ connect string copied to clipboard{RST}\n")

        # History kaydet
        if save:
            add_history({
                "query"   : query,
                "matched" : r["matched"],
                "server"  : r["server"],
                "address" : f"{r['ip']}:{r['port']}",
                "map"     : r["map"],
                "found_at": r["found_at"],
            })


def cmd_multi(servers):
    print(f"\n  {DIM}Enter player names separated by commas:{RST}")
    raw = input(f"  {C}Names > {RST}").strip()
    if not raw:
        return
    names = [n.strip() for n in raw.split(",") if n.strip()]
    for name in names:
        found = run_search(name, servers)
        print_results(found, name)

def cmd_track(query, servers):
    interval = 60
    print(f"\n  {Y}[TRACK] Tracking '{query}' — scanning every {interval}s. Ctrl+C to stop.{RST}\n")
    last_addr = None
    try:
        while True:
            found = run_search(query, servers, silent=True)
            now = datetime.now().strftime("%H:%M:%S")
            if found:
                r = found[0]
                addr = f"{r['ip']}:{r['port']}"
                if addr != last_addr:
                    print(f"\n  {G}{B}[{now}] FOUND: {r['matched']}{RST}")
                    print(f"         {Y}connect {addr}{RST}  |  {r['map']}  |  {r['players']}")
                    if copy_to_clipboard(f"connect {addr}"):
                        print(f"         {DIM}✓ copied to clipboard{RST}")
                    last_addr = addr
                    add_history({
                        "query"   : query,
                        "matched" : r["matched"],
                        "server"  : r["server"],
                        "address" : addr,
                        "map"     : r["map"],
                        "found_at": r["found_at"],
                    })
                else:
                    print(f"  {DIM}[{now}] Still on same server: {addr}{RST}")
            else:
                print(f"  {DIM}[{now}] '{query}' not found — retrying in {interval}s{RST}")
                last_addr = None
            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\n  {DIM}Tracking stopped.{RST}\n")

def cmd_history():
    history = load_json(HISTORY_FILE, [])
    if not history:
        print(f"\n  {DIM}No history yet.{RST}\n")
        return
    print(f"\n  {B}Recent searches:{RST}\n")
    for i, h in enumerate(history[:20], 1):
        print(f"  {DIM}{h['found_at']}{RST}  {C}{h['matched']}{RST}  →  {Y}{h['address']}{RST}  {DIM}{h['map']}{RST}")
    print()

def cmd_favs():
    favs = load_favorites()
    if not favs:
        print(f"\n  {DIM}No favorites saved. Use 'addfav <name>' to add.{RST}\n")
        return
    print(f"\n  {B}Favorites:{RST}\n")
    for i, f in enumerate(favs, 1):
        print(f"  {C}#{i}{RST}  {f}")
    print()

def cmd_addfav(name):
    favs = load_favorites()
    if name in favs:
        print(f"  {DIM}'{name}' already in favorites.{RST}\n")
        return
    favs.append(name)
    save_favorites(favs)
    print(f"  {G}✓ '{name}' added to favorites.{RST}\n")

def cmd_delfav(name):
    favs = load_favorites()
    if name not in favs:
        print(f"  {R}'{name}' not in favorites.{RST}\n")
        return
    favs.remove(name)
    save_favorites(favs)
    print(f"  {Y}✓ '{name}' removed from favorites.{RST}\n")

def cmd_scanfavs(servers):
    favs = load_favorites()
    if not favs:
        print(f"\n  {DIM}No favorites to scan.{RST}\n")
        return
    print(f"\n  {B}Scanning {len(favs)} favorite(s)...{RST}\n")
    for name in favs:
        found = run_search(name, servers)
        print_results(found, name)


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    srv_path   = os.path.join(script_dir, SERVER_LIST)
    servers    = load_servers(srv_path)

    clear()
    print_header()
    print(f"  {DIM}{len(servers)} servers loaded  |  type 'help' for commands{RST}\n")

    while True:
        try:
            raw = input(f"  {C}> {RST}").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{DIM}Exiting...{RST}")
            break

        if not raw:
            continue

        cmd = raw.lower()
        parts = raw.split(None, 1)

        if cmd in ("exit", "quit", "q"):
            print(f"{DIM}Exiting...{RST}")
            break
        elif cmd == "help":
            print_help()
        elif cmd == "history":
            cmd_history()
        elif cmd == "favs":
            cmd_favs()
        elif cmd == "multi":
            cmd_multi(servers)
        elif cmd == "scanfavs":
            cmd_scanfavs(servers)
        elif parts[0].lower() == "addfav" and len(parts) == 2:
            cmd_addfav(parts[1].strip())
        elif parts[0].lower() == "delfav" and len(parts) == 2:
            cmd_delfav(parts[1].strip())
        elif parts[0].lower() == "track" and len(parts) == 2:
            cmd_track(parts[1].strip(), servers)
        else:
            # Normal search
            found = run_search(raw, servers)
            print_results(found, raw)

        print(f"  {DIM}{'─'*48}{RST}\n")

if __name__ == "__main__":
    main()
