import warnings
import pandas as pd
import numpy as np

try:
    import streamlit as st
    _ST_AVAILABLE = True
except Exception:
    st = None
    _ST_AVAILABLE = False

def _st_info(msg, level="info"):
    """Affiche un message dans Streamlit si disponible, sinon print."""
    print(msg)
    if not _ST_AVAILABLE or st is None:
        return
    try:
        if level == "success":
            st.success(msg)
        elif level == "warning":
            st.warning(msg)
        elif level == "info":
            st.info(msg)
    except Exception:
        pass

try:
    from ortools.constraint_solver import routing_enums_pb2  # noqa
    from ortools.constraint_solver import pywrapcp            # noqa
    ORTOOLS_AVAILABLE = True
except Exception:
    routing_enums_pb2 = None
    pywrapcp          = None
    ORTOOLS_AVAILABLE = False


# ==========================================
# PARTIE 1 : FONCTIONS UTILITAIRES
# ==========================================

def minutes_to_hhmm(minutes):
    """Convertit des minutes depuis minuit en format HH:MM."""
    h = int(minutes // 60)
    m = int(minutes % 60)
    return f"{h:02d}:{m:02d}"


def generate_target_windows(sites_config):
    """
    Génère les fenêtres de passage pour chaque site.

    Règles :
    - freq = 1 (passage unique) : fenêtre libre [open, close].
      OR-Tools positionne la collecte librement dans cette plage,
      ce qui maximise la flexibilité de groupage des tournées.
    - freq > 1 :
        * 1er passage  : contrainte "au plus tôt" → [open,  open  + 20 min]
        * Dernier      : contrainte "au plus tôt" → [close, close + 20 min]
        * Intermédiaires : fenêtre centrée ±10 min autour du point théorique
    """
    tasks = []
    for site_name, config in sites_config.items():
        ouv, fer, freq = config['open'], config['close'], config['freq']

        if freq <= 1:
            # ── Passage unique : fenêtre entièrement libre ────────────────
            tasks.append({
                'site_name'  : str(site_name).strip().upper(),
                'window'     : (ouv, fer),   # toute la plage d'ouverture
                'target_time': (ouv + fer) / 2,
                'is_fixed'   : False,         # pas de contrainte horaire
                'freq_unique': True,
                'done'       : False
            })
        else:
            # ── Passages multiples : répartition linéaire contrainte ──────
            intervalle     = (fer - ouv) / (freq - 1)
            points_passage = [ouv + (i * intervalle) for i in range(freq)]
            marge_retard   = 20

            for i, cible in enumerate(points_passage):
                is_premier = (i == 0)
                is_dernier = (i == len(points_passage) - 1)

                if is_premier or is_dernier:
                    window = (cible, cible + marge_retard)
                else:
                    window = (cible - 10, cible + 10)

                tasks.append({
                    'site_name'  : str(site_name).strip().upper(),
                    'window'     : window,
                    'target_time': cible,
                    'is_fixed'   : is_premier or is_dernier,
                    'freq_unique': False,
                    'done'       : False
                })

    return sorted(tasks, key=lambda x: x['window'][0])


# ==========================================
# PARTIE 2 : CONSTRUCTION DU MODÈLE OR-TOOLS
# ==========================================

def _build_ortools_data(m_duree_df, tasks, temps_collecte, max_tournee, config_rh):
    """
    Traduit les données métier en structures attendues par OR-Tools.
    SCALE=10 → précision à 0,1 minute.
    """
    SCALE      = 10
    DEPOT_NAME = "HLS"

    df = m_duree_df.copy()
    nom_col = df.columns[0]
    df = df.set_index(nom_col)
    df.index   = df.index.astype(str).str.strip().str.upper()
    df.columns = df.columns.astype(str).str.strip().str.upper()

    node_names   = [DEPOT_NAME]
    node_to_task = [None]
    for task in tasks:
        node_names.append(task['site_name'])
        node_to_task.append(task)

    distance_matrix = []
    for from_site in node_names:
        row = []
        for to_site in node_names:
            if from_site == to_site:
                row.append(0)
            elif from_site in df.index and to_site in df.columns:
                row.append(int(round(float(df.loc[from_site, to_site]) * SCALE)))
            else:
                row.append(0)
        distance_matrix.append(row)

    time_windows = [(int(200 * SCALE), int(1440 * SCALE))]
    for task in tasks:
        time_windows.append((int(task['window'][0] * SCALE), int(task['window'][1] * SCALE)))

    service_times = [0] + [int(temps_collecte * SCALE) for _ in tasks]

    return {
        'distance_matrix'  : distance_matrix,
        'time_windows'     : time_windows,
        'service_times'    : service_times,
        'num_vehicles'     : len(tasks),
        'depot'            : 0,
        'node_to_task'     : node_to_task,
        'node_names'       : node_names,
        'SCALE'            : SCALE,
        'max_poste_scaled' : int(config_rh.get('amplitude', 450) * SCALE),
        'max_tournee_scaled': int(max_tournee * SCALE),
        'pause_seuil_scaled': int(180 * SCALE),
        'pause_duree_scaled': int(config_rh.get('pause', 30) * SCALE),
        'releve_scaled'    : int(config_rh.get('releve', 15) * SCALE),
        'n_tasks'          : len(tasks),
    }


def _solve_ortools(data, time_limit_seconds=30):
    """
    Résout le VRPTW avec OR-Tools.
    - Contrainte dure : pas de repassage sur un même site physique dans une tournée
      (souple : autorisé en dernier recours si OR-Tools ne trouve pas de solution sans).
    - Objectif : minimiser véhicules puis temps total.
    """
    SCALE = data['SCALE']

    manager = pywrapcp.RoutingIndexManager(
        len(data['distance_matrix']),
        data['num_vehicles'],
        data['depot']
    )
    routing = pywrapcp.RoutingModel(manager)

    # ── Callback transit + service ──────────────────────────────────────────
    def time_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node   = manager.IndexToNode(to_index)
        return data['distance_matrix'][from_node][to_node] + data['service_times'][from_node]

    cb_idx = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(cb_idx)

    # ── Dimension temporelle ────────────────────────────────────────────────
    routing.AddDimension(
        cb_idx,
        slack_max=int(90 * SCALE),
        capacity=int(1440 * SCALE),
        fix_start_cumul_to_zero=False,
        name='Time'
    )
    time_dim = routing.GetDimensionOrDie('Time')

    for node_idx in range(1, len(data['time_windows'])):
        idx = manager.NodeToIndex(node_idx)
        tw_open, tw_close = data['time_windows'][node_idx]
        time_dim.CumulVar(idx).SetRange(tw_open, tw_close)

    depot_idx = manager.NodeToIndex(data['depot'])
    time_dim.CumulVar(depot_idx).SetRange(*data['time_windows'][0])

    # ── Amplitude max de tournée ────────────────────────────────────────────
    solver = routing.solver()
    for v in range(data['num_vehicles']):
        solver.Add(
            time_dim.CumulVar(routing.End(v)) - time_dim.CumulVar(routing.Start(v))
            <= data['max_tournee_scaled']
        )

    # ── Contrainte : pas de repassage sur le même site physique (souple) ───
    # On identifie les groupes de nœuds qui représentent le même site physique.
    # Pour chaque véhicule, on impose qu'au plus 1 nœud du groupe soit visité.
    # Si OR-Tools ne trouve pas de solution avec cette contrainte, on relâche.
    node_names = data['node_names']
    depot_name = node_names[0]

    # Groupes : site_physique → liste d'index nœuds (hors dépôt)
    site_to_nodes = {}
    for node_idx, name in enumerate(node_names):
        if node_idx == 0:  # dépôt
            continue
        site_to_nodes.setdefault(name, []).append(node_idx)

    # Pénalité souple : si un site a plusieurs passages possibles dans une tournée,
    # on pénalise fortement (mais pas interdit) le fait d'en faire 2 dans la même.
    # On utilise AddDisjunction avec penalty=0 pour rendre chaque passage optionnel
    # puis on laisse OR-Tools choisir — la contrainte stricte est gérée par
    # un callback d'arc qui met le coût à l'infini si même site déjà visité.
    # Solution compatible OR-Tools : on ajoute une dimension "visite" par site
    # avec capacité 1 par véhicule (contrainte dure, souple si infaisable).
    penalite_repassage = int(5e6)  # Forte pénalité mais pas infinie

    for site, nodes in site_to_nodes.items():
        if len(nodes) <= 1:
            continue  # freq=1 : pas de risque de repassage
        # Pour chaque paire de nœuds du même site, on interdit qu'un véhicule
        # les visite tous les deux (coût prohibitif sur l'arc fictif).
        # En OR-Tools, on le modélise via une dimension de comptage par site.
        # Approche la plus simple et robuste : AllowedAssignments ou dimension comptage.
        # On utilise ici une dimension entière "count_{site}" avec capacité 1.
        dim_name = f"cnt_{site[:8].replace(' ', '_')}"
        count_cb_vals = [0] * len(node_names)
        for n in nodes:
            count_cb_vals[n] = 1  # chaque visite de ce site compte pour 1

        def make_count_cb(vals):
            def cb(from_idx, to_idx):
                return vals[manager.IndexToNode(to_idx)]
            return cb

        cnt_cb = routing.RegisterTransitCallback(make_count_cb(count_cb_vals))
        routing.AddDimensionWithVehicleCapacity(
            cnt_cb,
            0,       # pas de slack
            [1] * data['num_vehicles'],   # max 1 passage par site et par véhicule
            True,    # start cumul to zero
            dim_name
        )

    # ── Pauses obligatoires ─────────────────────────────────────────────────
    for v in range(data['num_vehicles']):
        break_start = solver.IntVar(
            data['pause_seuil_scaled'],
            data['max_poste_scaled'],
            f'break_start_v{v}'
        )
        break_iv = solver.FixedDurationIntervalVar(
            break_start, data['pause_duree_scaled'], f'break_iv_v{v}'
        )
        time_dim.SetBreakIntervalsOfVehicle(
            [break_iv], v,
            node_visit_transits=[int(5 * SCALE)] * len(data['distance_matrix'])
        )

    # ── Objectif ────────────────────────────────────────────────────────────
    for v in range(data['num_vehicles']):
        routing.SetFixedCostOfVehicle(int(1e8), v)

    # ── Résolution (mode strict : contrainte de repassage active) ───────────
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.seconds = time_limit_seconds
    params.log_search = False

    solution = routing.SolveWithParameters(params)

    if solution:
        return manager, routing, solution, time_dim, False  # False = pas de repassage

    # ── Fallback souple : on relâche la contrainte de repassage ─────────────
    warnings.warn(
        "Contrainte anti-repassage relâchée (fenêtres incompatibles).",
        RuntimeWarning, stacklevel=3
    )
    # On recrée un modèle sans les dimensions de comptage
    manager2 = pywrapcp.RoutingIndexManager(
        len(data['distance_matrix']), data['num_vehicles'], data['depot']
    )
    routing2 = pywrapcp.RoutingModel(manager2)
    cb_idx2  = routing2.RegisterTransitCallback(
        lambda fi, ti: (data['distance_matrix'][manager2.IndexToNode(fi)][manager2.IndexToNode(ti)]
                        + data['service_times'][manager2.IndexToNode(fi)])
    )
    routing2.SetArcCostEvaluatorOfAllVehicles(cb_idx2)
    routing2.AddDimension(cb_idx2, int(90*SCALE), int(1440*SCALE), False, 'Time')
    time_dim2 = routing2.GetDimensionOrDie('Time')

    for node_idx in range(1, len(data['time_windows'])):
        idx = manager2.NodeToIndex(node_idx)
        tw_open, tw_close = data['time_windows'][node_idx]
        time_dim2.CumulVar(idx).SetRange(tw_open, tw_close)
    time_dim2.CumulVar(manager2.NodeToIndex(data['depot'])).SetRange(*data['time_windows'][0])

    solver2 = routing2.solver()
    for v in range(data['num_vehicles']):
        solver2.Add(
            time_dim2.CumulVar(routing2.End(v)) - time_dim2.CumulVar(routing2.Start(v))
            <= data['max_tournee_scaled']
        )
        routing2.SetFixedCostOfVehicle(int(1e8), v)

    solution2 = routing2.SolveWithParameters(params)
    if solution2:
        return manager2, routing2, solution2, time_dim2, True  # True = repassage autorisé
    return None, None, None, None, False


# ==========================================
# PARTIE 3 : EXTRACTION ET FORMATAGE
# ==========================================

def _extract_tournees(manager, routing, solution, time_dimension, data):
    """Extrait les tournées OR-Tools au format interne [{"site", "heure"}, ...]."""
    SCALE      = data['SCALE']
    depot_name = data['node_names'][0]
    tournees   = []

    for v in range(data['num_vehicles']):
        index = routing.Start(v)
        route = []
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            t    = solution.Min(time_dimension.CumulVar(index)) / SCALE
            route.append({'site': data['node_names'][node], 'heure': t})
            index = solution.Value(routing.NextVar(index))

        node = manager.IndexToNode(index)
        t    = solution.Min(time_dimension.CumulVar(index)) / SCALE
        route.append({'site': data['node_names'][node], 'heure': t})

        if all(s['site'] == depot_name for s in route):
            continue
        tournees.append(route)

    return tournees


# ==========================================
# PARTIE 4 : MÉTRIQUES D'OPTIMISATION
# ==========================================

def _calculer_score(flotte, config_rh, temps_collecte):
    """
    Calcule les métriques d'une solution pour comparer les itérations.

    Critère d'optimisation (par ordre de priorité) :
      1. Minimiser le nombre de véhicules
      2. Minimiser le nombre de postes chauffeurs
      3. À iso (véhicules, postes) : maximiser le taux du poste le plus chargé,
         puis du second, etc. (tri lexicographique descendant des taux).
         → "remplir le premier poste avant d'ouvrir le suivant"

    Taux d'occupation d'un poste = (conduite + collecte) / amplitude_max_poste

    Retourne un dict :
        nb_vehicules    : int
        nb_postes       : int
        taux_occupation : float   moyenne des taux (pour affichage)
        taux_par_poste  : list    taux individuels triés décroissant
        score_tri       : tuple   clé de comparaison
    """
    MAX_POSTE    = config_rh.get('amplitude', 450)
    nb_vehicules = len(flotte)
    nb_postes    = 0
    taux_liste   = []

    for vacations in flotte.values():
        for vacation in vacations:
            nb_postes += 1

            # Temps productif du poste = conduite + collecte (hors attente)
            temps_productif = 0.0
            for tournee in vacation:
                for i in range(len(tournee) - 1):
                    if tournee[i]['site'] != 'HLS':
                        temps_productif += temps_collecte
                    temps_productif += max(
                        0,
                        tournee[i+1]['heure'] - tournee[i]['heure'] - temps_collecte
                    )

            taux = min(temps_productif / MAX_POSTE, 1.0)
            taux_liste.append(taux)

    # Tri décroissant : le poste le plus chargé en premier
    taux_tries  = sorted(taux_liste, reverse=True)
    taux_moyen  = sum(taux_liste) / len(taux_liste) if taux_liste else 0.0

    # Score : minimiser (vehicules, postes) puis maximiser lexicographiquement
    # les taux du plus chargé au moins chargé → on les négative pour le min
    score_tri = (nb_vehicules, nb_postes) + tuple(-round(t, 4) for t in taux_tries)

    return {
        'nb_vehicules'   : nb_vehicules,
        'nb_postes'      : nb_postes,
        'taux_occupation': taux_moyen,
        'taux_par_poste' : taux_tries,
        'score_tri'      : score_tri,
    }


def _est_meilleure(nouvelle, meilleure):
    """Retourne True si 'nouvelle' est meilleure que 'meilleure'."""
    if meilleure is None:
        return True
    return nouvelle['score_tri'] < meilleure['score_tri']


# ==========================================
# PARTIE 5 : AFFECTATION VÉHICULES / POSTES
# ==========================================

def assign_to_vehicles(tournees, config_rh):
    """Répartit les tournées par véhicule et par chauffeur (vacation)."""
    MAX_POSTE = config_rh.get('amplitude', 450)
    PAUSE     = config_rh.get('pause', 30)
    RELEVE    = config_rh.get('releve', 15)

    tournees_triees  = sorted(tournees, key=lambda x: x[0]['heure'])
    flotte_vehicules = {}

    for trne in tournees_triees:
        debut_trne = trne[0]['heure']
        fin_trne   = trne[-1]['heure']
        assigned   = False

        for v_id, postes in flotte_vehicules.items():
            dernier_poste = postes[-1]
            h_debut_poste = dernier_poste[0][0]['heure']
            h_fin_poste   = dernier_poste[-1][-1]['heure']

            if (fin_trne - h_debut_poste) <= MAX_POSTE:
                marge = PAUSE if (h_fin_poste - h_debut_poste) > 180 else 0
                if h_fin_poste + marge <= debut_trne:
                    dernier_poste.append(trne)
                    assigned = True
                    break
            elif h_fin_poste + RELEVE <= debut_trne:
                postes.append([trne])
                assigned = True
                break

        if not assigned:
            v_num = len(flotte_vehicules) + 1
            flotte_vehicules[f"Véhicule {v_num}"] = [[trne]]

    return flotte_vehicules


def optimiser_postes_chauffeurs(flotte, config_rh, souplesse=False):
    """Fusionne les vacations pour réduire le nombre de chauffeurs sans ajouter de véhicules."""
    MAX_POSTE = config_rh.get('amplitude', 450)
    RELEVE    = config_rh.get('releve', 15)

    toutes_vacations = []
    for vacations in flotte.values():
        toutes_vacations.extend(vacations)
    toutes_vacations.sort(key=lambda x: x[0][0]['heure'])

    nouvelle_flotte = {f"Véhicule {i+1}": [] for i in range(len(flotte))}

    for vac in toutes_vacations:
        debut_v = vac[0][0]['heure']
        fin_v   = vac[-1][-1]['heure']
        placed  = False

        for postes in nouvelle_flotte.values():
            for poste in postes:
                h_dep_p = poste[0][0]['heure']
                h_fin_p = poste[-1][-1]['heure']
                amp = max(h_fin_p, fin_v) - min(h_dep_p, debut_v)

                if amp <= MAX_POSTE:
                    if souplesse:
                        for decalage in range(20, 31):
                            if fin_v + decalage >= h_dep_p and debut_v <= h_fin_p + decalage:
                                poste.extend(vac)
                                poste.sort(key=lambda x: x[0]['heure'])
                                placed = True
                                break
                    else:
                        if fin_v + 5 <= h_dep_p or debut_v >= h_fin_p + 5:
                            poste.extend(vac)
                            poste.sort(key=lambda x: x[0]['heure'])
                            placed = True
                if placed:
                    break
            if placed:
                break

        if not placed:
            for v_id, postes in nouvelle_flotte.items():
                if not postes:
                    postes.append(vac)
                    placed = True
                    break
                conflit = any(
                    not (fin_v + RELEVE <= p[0][0]['heure'] or debut_v >= p[-1][-1]['heure'] + RELEVE)
                    for p in postes
                )
                if not conflit:
                    postes.append(vac)
                    postes.sort(key=lambda x: x[0][0]['heure'])
                    placed = True
                    break

        if not placed:
            nouvelle_flotte[list(nouvelle_flotte.keys())[0]].append(vac)

    return {k: v for k, v in nouvelle_flotte.items() if v}


# ==========================================
# PARTIE 6 : FALLBACK GREEDY
# ==========================================

def _greedy_fallback(m_duree_df, tasks, temps_collecte, max_tournee):
    """Heuristique gloutonne de secours si OR-Tools est indisponible."""
    df = m_duree_df.copy()
    nom_col = df.columns[0]
    df = df.set_index(nom_col)
    df.index   = df.index.astype(str).str.strip().str.upper()
    df.columns = df.columns.astype(str).str.strip().str.upper()

    DEPOT      = "HLS"
    tasks_copy = [t.copy() for t in tasks]
    tournees   = []

    while any(not t['done'] for t in tasks_copy):
        remaining = [t for t in tasks_copy if not t['done']]
        if not remaining:
            break

        first_task = remaining[0]
        site_cible = first_task['site_name']
        if site_cible not in df.index:
            first_task['done'] = True
            continue

        heure_depart = max(300, first_task['window'][0] - df.loc[DEPOT, site_cible])
        current_time = heure_depart
        tournee      = [{'site': DEPOT, 'heure': current_time}]
        current_site = DEPOT
        sites_visites = set()  # contrainte anti-repassage (souple via exclusion)

        while True:
            best_idx, score_min = None, float('inf')

            for idx, task in enumerate(tasks_copy):
                t_site = task['site_name']
                if task['done'] or t_site not in df.index or t_site in sites_visites:
                    continue
                trajet  = df.loc[current_site, t_site]
                retour  = df.loc[t_site, DEPOT]
                arrivee = current_time + trajet
                debut   = max(arrivee, task['window'][0])
                fin     = debut + temps_collecte

                if (fin + retour - tournee[0]['heure']) <= max_tournee:
                    attente = max(0, task['window'][0] - arrivee)
                    score   = attente + (trajet * 2)
                    if score < score_min:
                        score_min, best_idx = score, idx

            if best_idx is not None:
                task   = tasks_copy[best_idx]
                t_site = task['site_name']
                h_reel = max(current_time + df.loc[current_site, t_site], task['window'][0])
                tournee.append({'site': t_site, 'heure': h_reel})
                current_time = h_reel + temps_collecte
                task['done'] = True
                current_site = t_site
                sites_visites.add(t_site)  # ← bloque le repassage dans cette tournée
                for autre in df.columns:
                    if df.loc[t_site, autre] == 0:
                        sites_visites.add(autre)
            else:
                tournee.append({'site': DEPOT, 'heure': current_time + df.loc[current_site, DEPOT]})
                break

        tournees.append(tournee)

    return tournees


# ==========================================
# PARTIE 7 : MOTEUR PRINCIPAL (point d'entrée)
# ==========================================

def run_optimization(
    m_duree_df,
    sites_config,
    temps_collecte,
    max_tournee,
    config_rh=None,
    souplesse=False,
    time_limit_seconds=15
):
    """
    Optimise les tournées de biologie par recherche itérative sur la durée max.

    Stratégie :
      - Teste tous les paliers de durée_max entre 60 min et max_tournee (pas 15 min)
      - Pour chaque palier : résolution OR-Tools (ou greedy si indisponible)
      - Retient la solution qui minimise (nb_vehicules, nb_postes, -taux_occupation)
      - Contrainte anti-repassage : dure par défaut, souple en dernier recours

    Interface inchangée — compatible app.py, param_bio.py, resultats_bio.py.
    """
    # ── Config RH ────────────────────────────────────────────────────────────
    if config_rh is None:
        try:
            import streamlit as _st
            config_rh = _st.session_state.get("biologie_config", {}).get(
                "rh", {"amplitude": 450, "pause": 30, "releve": 15}
            )
        except Exception:
            config_rh = {"amplitude": 450, "pause": 30, "releve": 15}

    # ── Préparation commune ───────────────────────────────────────────────────
    clean_config = {str(k).strip().upper(): v for k, v in sites_config.items()}
    tasks        = generate_target_windows(clean_config)

    # ── Plage de test : 60 → max_tournee, pas 15 min ─────────────────────────
    paliers = list(range(60, max_tournee + 1, 15))
    if max_tournee not in paliers:
        paliers.append(max_tournee)

    meilleure_flotte  = None
    meilleur_score    = None
    meilleur_palier   = None
    repassage_autorise = False

    _st_info(f"🔍 Recherche itérative sur {len(paliers)} paliers "
             f"({paliers[0]}–{paliers[-1]} min, pas 15 min)...", "info")

    for palier in paliers:
        tournees_unitaires = None

        if ORTOOLS_AVAILABLE:
            data = _build_ortools_data(m_duree_df, tasks, temps_collecte, palier, config_rh)
            manager, routing, solution, time_dim, repassage = _solve_ortools(
                data, time_limit_seconds=time_limit_seconds
            )
            if solution is not None:
                tournees_unitaires = _extract_tournees(manager, routing, solution, time_dim, data)
                repassage_autorise = repassage

        if tournees_unitaires is None:
            tournees_unitaires = _greedy_fallback(m_duree_df, tasks, temps_collecte, palier)

        if not tournees_unitaires:
            continue

        flotte   = assign_to_vehicles(tournees_unitaires, config_rh)
        flotte   = optimiser_postes_chauffeurs(flotte, config_rh, souplesse=souplesse)
        score    = _calculer_score(flotte, config_rh, temps_collecte)

        if _est_meilleure(score, meilleur_score):
            meilleure_flotte = flotte
            meilleur_score   = score
            meilleur_palier  = palier

    # ── Rapport final ─────────────────────────────────────────────────────────
    if meilleur_score is not None:
        solveur = "OR-Tools" if ORTOOLS_AVAILABLE else "heuristique gloutonne"
        repass  = " (repassage autorisé sur certains sites)" if repassage_autorise else ""
        _st_info(
            f"✅ Meilleure solution ({solveur}{repass}) — "
            f"palier {meilleur_palier} min | "
            f"{meilleur_score['nb_vehicules']} véhicule(s) | "
            f"{meilleur_score['nb_postes']} poste(s) | "
            f"taux occupation moyen {meilleur_score['taux_occupation']:.1%}",
            "success"
        )
        # Stocker le rapport dans session_state pour affichage après st.rerun()
        try:
            import streamlit as _st
            _st.session_state["bio_rapport"] = {
                "nb_vehicules"   : meilleur_score["nb_vehicules"],
                "nb_postes"      : meilleur_score["nb_postes"],
                "taux_occupation": float(meilleur_score["taux_occupation"]),
                "taux_par_poste" : [float(t) for t in meilleur_score.get("taux_par_poste", [])],
                "palier"         : meilleur_palier,
                "repassage"      : repassage_autorise,
                "solveur"        : solveur,
            }
        except Exception:
            pass
    else:
        _st_info("⚠️ Aucune solution trouvée sur tous les paliers.", "warning")

    return meilleure_flotte or {}
