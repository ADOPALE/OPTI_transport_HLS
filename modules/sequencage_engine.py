import pandas as pd
import math
import streamlit as st
from datetime import time, datetime, timedelta

# =================================================================
# 1. UTILITAIRES & LOGIQUE DE SÉLECTION
# =================================================================

def to_min(t):
    if isinstance(t, (time, datetime)):
        return t.hour * 60 + t.minute
    return float(t)

def get_couloir_id(sj):
    pts = sorted([sj.points_depart[0], sj.points_arrivee[-1]])
    return f"{pts[0]}--{pts[1]}"

def calculer_stress_maillon_critique(sj, minute_actuelle, matrice_duree, p_position_actuelle):
    dist_approche = matrice_duree.get(p_position_actuelle, {}).get(sj.points_depart[0], 0)
    lst_sj = 0
    if sj.type_logistique in ['GROUPAGE_PUR', 'RAMASSAGE']:
        h_deadline_min = min(to_min(j.h_deadline) for j in sj.liste_jobs)
        lst_sj = h_deadline_min - sj.poids_total
    else:
        lst_candidats = []
        temps_cumule_trajets = 0
        pos_precedente = sj.points_depart[0]
        for job in sj.liste_jobs:
            trajet_interne = matrice_duree.get(pos_precedente, {}).get(job.origin, 0)
            temps_cumule_trajets += trajet_interne
            duree_propre = (job.poids_total if hasattr(job, 'poids_total') else 30)
            lst_job = to_min(job.h_deadline) - (temps_cumule_trajets + duree_propre)
            lst_candidats.append(lst_job)
            pos_precedente = job.destination
            temps_cumule_trajets += duree_propre
        lst_sj = min(lst_candidats)
    
    marge_depart = lst_sj - (minute_actuelle + dist_approche)
    return 1000 - marge_depart

def obtenir_couloir_groupage_prioritaire(jobs_restants):
    stats_couloirs = {}
    for sj in jobs_restants:
        if sj.type_logistique == 'GROUPAGE_PUR':
            zone_dep, zone_arr = sj.points_depart[0][:3], sj.points_arrivee[-1][:3]
            c_id = f"{zone_dep}--{zone_arr}"
            stats_couloirs[c_id] = stats_couloirs.get(c_id, 0) + len(sj.liste_jobs)
    return max(stats_couloirs, key=stats_couloirs.get) if stats_couloirs else None

# =================================================================
# 2. CLASSE POSTE CHAUFFEUR
# =================================================================

class PosteChauffeur:
    def __init__(self, id_p, v_type, site_depot, params_rh):
        self.id_poste = id_p
        self.vehicule_type = v_type
        self.stationnement_initial = site_depot
        self.position_actuelle = site_depot
        self.etat = 'INACTIF'
        self.temps_restant_etat = 0
        self.job_en_cours = None
        self.couloir_actuel = None
        self.h_debut_service_actuel = None 
        self.pause_faite = False
        self.historique = []
        self.amplitude_max = params_rh.get('amplitude_totale', 450)
        self.duree_pause = params_rh.get('pause', 20)
        self.temps_passation = params_rh.get('temps_fixes_fin', 15)
        self.temps_prise = params_rh.get('temps_fixes_prise', 15)
        self.vehicule_deja_affecte = False
        self.marge_inter_job = 0  # initialisée depuis params_logistique
        
    def enregistrer(self, minute, activite, sj=None, details=""):
        sj_id = sj.super_job_id if sj else "N/A"
        sj_poids = sj.poids_total if sj else 0
        self.historique.append({
            "Minute_Debut": minute,
            "Heure_Debut": f"{int(minute//60):02d}h{int(minute%60):02d}",
            "Activite": activite,
            "SJ_ID": sj_id,
            "sj_poids": sj_poids,
            "Details": details,
            # Position au moment de l'enregistrement — utile pour l'affichage Gantt
            "position_depart": self.position_actuelle,
        })

# =================================================================
# 3. MOTEUR DE SIMULATION
# =================================================================

def selectionner_meilleur_job(p, dispos, minute, matrice_duree, nb_Jobs, jobs_restants,
                              est_premier_job=False, jobs_reserves=None):
    """
    Sélectionne le meilleur SJ pour le poste p parmi dispos.
    jobs_reserves : dict {sj_id → poste_reservataire} — les jobs réservés pour
    un poste futur plus pertinent sont exclus sauf si p est le poste reservataire.
    """
    if not dispos: return None
    if jobs_reserves is None: jobs_reserves = {}

    # Filtrer les jobs réservés pour un autre poste
    dispos_filtres = [
        sj for sj in dispos
        if sj.super_job_id not in jobs_reserves          # pas réservé
        or jobs_reserves[sj.super_job_id] is p           # ou réservé pour MOI
    ]
    if not dispos_filtres:
        # Tous les jobs sont réservés pour d'autres — on tente quand même sur
        # les jobs les plus urgents (évite le blocage total)
        dispos_filtres = dispos

    liste_candidats = []
    for sj in dispos_filtres:
        stress = calculer_stress_maillon_critique(sj, minute, matrice_duree, p.position_actuelle)
        liste_candidats.append({'sj': sj, 'stress': stress})

    liste_candidats.sort(key=lambda x: x['stress'], reverse=True)
    top_n_jobs = [item['sj'] for item in liste_candidats[:nb_Jobs]]

    # Priorité 1 : job réservé pour CE poste parmi les top-N (urgence absolue)
    for sj in top_n_jobs:
        if jobs_reserves.get(sj.super_job_id) is p:
            return sj

    # Priorité 2 : même couloir ET même position
    couloir_precedent = p.couloir_actuel
    for sj in top_n_jobs:
        if couloir_precedent and get_couloir_id(sj) == couloir_precedent and sj.points_depart[0] == p.position_actuelle:
            return sj
    # Priorité 3 : même position exacte
    for sj in top_n_jobs:
        if sj.points_depart[0] == p.position_actuelle: return sj
    # Priorité 4 : même zone géographique
    for sj in top_n_jobs:
        if sj.points_depart[0][:3] == p.position_actuelle[:3]: return sj
    # Priorité 5 : le plus proche
    best_sj_proximite, dist_min = None, float('inf')
    for sj in top_n_jobs:
        dist = matrice_duree.get(p.position_actuelle, {}).get(sj.points_depart[0], 0)
        if dist < dist_min:
            dist_min, best_sj_proximite = dist, sj
    return best_sj_proximite


SEUIL_STRESS_RESERVATION = 950   # jobs avec deadline dans < 50 min
SEUIL_LOOK_AHEAD_MIN     = 15    # on regarde jusqu'à 15 min dans le futur

def estimer_disponibilite_poste(p, minute, matrice):
    """
    Retourne (minute_dispo, position_dispo) :
    la minute à laquelle le poste sera libre et sa position à ce moment.
    - Poste DISPONIBLE/INACTIF → disponible maintenant, position actuelle
    - Poste EN_MISSION → libre à minute + temps_restant, à la destination du SJ
    - Poste EN_TRAJET_VIDE → libre à minute + temps_restant + poids SJ, à la destination
    - Autres états → estimation conservative : minute + temps_restant
    """
    if p.etat in ('DISPONIBLE', 'INACTIF', 'INTER_JOB', 'EN_RETOUR_DEPOT'):
        return minute, p.position_actuelle
    if p.etat == 'EN_MISSION' and p.job_en_cours:
        m_dispo = minute + p.temps_restant_etat
        pos_dispo = p.job_en_cours.points_arrivee[-1]
        return m_dispo, pos_dispo
    if p.etat == 'EN_TRAJET_VIDE' and p.job_en_cours:
        # Arrive sur site de départ dans temps_restant, puis fait la mission
        m_dispo = minute + p.temps_restant_etat + p.job_en_cours.poids_total
        pos_dispo = p.job_en_cours.points_arrivee[-1]
        return m_dispo, pos_dispo
    # Cas génériques (PAUSE, PRISE_POSTE, PASSATION…)
    return minute + p.temps_restant_etat, p.position_actuelle


def evaluer_faisabilite_globale(postes, jobs_restants, minute, matrice):
    """
    Pour chaque job urgent (stress > SEUIL_STRESS_RESERVATION), détermine
    le meilleur poste capable de le traiter (maintenant ou dans les 15 min).

    Retourne jobs_reserves : dict {super_job_id → meilleur_poste}
    Un job est réservé si :
      - Le meilleur poste n'est pas disponible maintenant (delta > 0)
      - Mais il sera disponible dans <= SEUIL_LOOK_AHEAD_MIN minutes
      - Et la livraison sera terminée avant h_deadline_min
    Si le meilleur poste est déjà disponible maintenant, on ne réserve pas
    (il prendra le job naturellement via selectionner_meilleur_job).
    """
    jobs_reserves = {}   # {sj.super_job_id: poste_reservataire}

    postes_actifs = [p for p in postes
                     if p.etat not in ('OPTIMISATION_AM', 'FIN_DE_SERVICE')
                     and p.h_debut_service_actuel is not None]

    for sj in jobs_restants:
        # Filtrer sur les jobs urgents uniquement
        stress = calculer_stress_maillon_critique(sj, minute, matrice,
                                                  postes_actifs[0].position_actuelle if postes_actifs else '')
        if stress <= SEUIL_STRESS_RESERVATION:
            continue

        deadline = to_min(sj.h_deadline_min)
        meilleur_poste  = None
        meilleur_score  = float('inf')   # on minimise (minute_fin_livraison)
        poste_dispo_now = None           # meilleur poste disponible maintenant

        for p in postes_actifs:
            # Vérifier que le poste a encore de l'amplitude
            h_limite = p.h_debut_service_actuel + p.amplitude_max - p.temps_passation
            m_dispo, pos_dispo = estimer_disponibilite_poste(p, minute, matrice)
            delta = m_dispo - minute

            # On ne regarde pas au-delà du seuil look-ahead
            if delta > SEUIL_LOOK_AHEAD_MIN:
                continue

            approche = matrice.get(pos_dispo, {}).get(sj.points_depart[0], 0)
            m_fin = m_dispo + approche + sj.poids_total

            # Vérifier faisabilité : livraison avant deadline ET avant fin de poste
            if m_fin > deadline or m_fin > h_limite:
                continue

            if delta == 0:
                # Poste disponible maintenant — candidat naturel, noter le meilleur
                if m_fin < meilleur_score:
                    meilleur_score = m_fin
                    poste_dispo_now = p
            else:
                # Poste futur — candidat à réservation
                if m_fin < meilleur_score:
                    meilleur_score = m_fin
                    meilleur_poste = p

        # Décision de réservation :
        # On réserve uniquement si le meilleur poste futur fait mieux que
        # tous les postes disponibles maintenant.
        if meilleur_poste and (poste_dispo_now is None or meilleur_score < poste_dispo_now.h_debut_service_actuel):
            jobs_reserves[sj.super_job_id] = meilleur_poste

    return jobs_reserves

def simuler_faisabilite(I_matin, I_am, prio_tension, liste_sj_type, v_type, matrice_duree, params_logistique, df_vehicules):
    rh = params_logistique.get('rh', {})
    h_start = to_min(rh.get('h_prise_min', 360))
    h_end = to_min(rh.get('h_fin_max', 1380))
    h_bascule = h_start + to_min(rh.get('amplitude_totale', 450)) - 100
    
    facteur_alea = 1 + (params_logistique.get('alea_circulation', 0) / 100)
    matrice_travail = {o: {d: dur * facteur_alea for d, dur in dests.items()} for o, dests in matrice_duree.items()}
    
    filtre = df_vehicules[df_vehicules['Types'] == v_type]
    depot_initial = filtre['Stationnement initial'].iloc[0] if not filtre.empty else "HSJ"

    marge_inter_job = params_logistique.get('marge_inter_job', 0)
    postes = [PosteChauffeur(f"{v_type}_{i+1}", v_type, depot_initial, rh) for i in range(I_matin)]
    for p in postes:
        p.marge_inter_job = marge_inter_job
    jobs_restants = list(liste_sj_type)
    minute = h_start

    while minute <= h_end:
        for p in postes:
            if p.etat == 'OPTIMISATION_AM': continue
            # EN_RETOUR_DEPOT est interruptible : on décrémente mais on ne skip pas
            # la suite de la boucle, pour réévaluer les jobs à chaque minute.
            if p.etat == 'EN_RETOUR_DEPOT':
                if p.temps_restant_etat > 0:
                    p.temps_restant_etat -= 1
                # Si le compteur atteint 0, le bloc EN_RETOUR_DEPOT ci-dessous
                # mettra le poste en DISPONIBLE pour évaluation immédiate.
                if p.temps_restant_etat > 0:
                    # Encore en route : réévaluer si un job urgent est apparu
                    pass  # continue vers les blocs d'état ci-dessous
            elif p.temps_restant_etat > 0:
                p.temps_restant_etat -= 1
                continue
            
            if p.etat == 'PRISE_POSTE':
                p.etat, p.vehicule_deja_affecte = 'DISPONIBLE', True
            elif p.etat == 'EN_TRAJET_VIDE':
                # EN_TRAJET_VIDE = trajet vers un job. À l'arrivée on démarre la mission.
                p.position_actuelle = p.job_en_cours.points_depart[0]
                p.etat, p.temps_restant_etat = 'EN_MISSION', p.job_en_cours.poids_total
                p.enregistrer(minute, "EN_MISSION", p.job_en_cours)
            elif p.etat == 'EN_RETOUR_DEPOT':
                # EN_RETOUR_DEPOT = retour sans mission. À l'arrivée le poste
                # repasse en DISPONIBLE — sauf si la pause est encore due,
                # auquel cas on la déclenche immédiatement pour éviter la boucle infinie.
                p.position_actuelle = p.stationnement_initial
                idx_p = int(p.id_poste.split('_')[-1])
                if minute >= h_bascule and idx_p > I_am:
                    p.etat, p.temps_restant_etat = 'OPTIMISATION_AM', 9999
                    p.enregistrer(minute, "VEHICULE_LIBERE", details="Désengagement (Optimisation AM)")
                    continue
                if p.h_debut_service_actuel is not None:
                    temps_trav_arrivee = minute - p.h_debut_service_actuel
                    if temps_trav_arrivee >= p.amplitude_max // 2 and not p.pause_faite:
                        p.etat, p.temps_restant_etat, p.pause_faite = 'EN_PAUSE', p.duree_pause, True
                        p.enregistrer(minute, "EN_PAUSE", details=f"Durée: {p.duree_pause}min")
                        continue
                p.etat = 'DISPONIBLE'  # la boucle DISPONIBLE gère la suite
            elif p.etat == 'EN_MISSION':
                p.position_actuelle = p.job_en_cours.points_arrivee[-1]
                p.couloir_actuel = get_couloir_id(p.job_en_cours)
                p.job_en_cours = None
                if p.marge_inter_job > 0:
                    p.etat, p.temps_restant_etat = 'INTER_JOB', p.marge_inter_job
                    p.enregistrer(minute, "INTER_JOB", details=f"Marge inter-job: {p.marge_inter_job}min")
                else:
                    p.etat = 'DISPONIBLE' 
            elif p.etat == 'EN_PAUSE': p.etat = 'DISPONIBLE'
            elif p.etat == 'INTER_JOB': p.etat = 'DISPONIBLE'
            elif p.etat == 'FIN_DE_SERVICE' and p.temps_restant_etat == 0 :
                p.etat, p.h_debut_service_actuel, p.pause_faite, p.couloir_actuel = 'INACTIF', None, False, None
                p.enregistrer(minute, "VEHICULE_LIBERE")

        # ── Visibilité anticipée des jobs ────────────────────────────────────────
        def job_visible(sj, poste):
            pos = poste.position_actuelle
            trajet_approche = matrice_travail.get(pos, {}).get(sj.points_depart[0], 0)
            anticipation = trajet_approche
            if poste.etat == 'INACTIF':
                anticipation += poste.temps_prise
            return minute >= to_min(sj.h_dispo_min) - anticipation

        dispos = [j for j in jobs_restants if any(
            job_visible(j, p) for p in postes
            if p.etat not in ['OPTIMISATION_AM', 'FIN_DE_SERVICE']
        )]

        # ── Évaluation globale et réservations look-ahead ────────────────────────
        # Pour les jobs urgents (stress > 950), on identifie le meilleur poste
        # futur (≤15 min) et on réserve le job pour lui si aucun poste disponible
        # maintenant ne peut faire mieux.
        jobs_reserves = evaluer_faisabilite_globale(
            postes, jobs_restants, minute, matrice_travail
        )
        for p in postes:
            if p.etat == 'OPTIMISATION_AM' or p.temps_restant_etat > 0: continue
            
            idx_p = int(p.id_poste.split('_')[-1])
            if p.etat == 'DISPONIBLE' and minute >= h_bascule and idx_p > I_am:
                if p.position_actuelle == p.stationnement_initial:
                    p.etat, p.temps_restant_etat = 'OPTIMISATION_AM', 9999
                    p.enregistrer(minute, "VEHICULE_LIBERE", details="Désengagement (Optimisation AM)")
                else:
                    p.etat = 'EN_RETOUR_DEPOT'
                    p.temps_restant_etat = matrice_travail.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                    p.enregistrer(minute, "RETOUR_DEPOT", details="Retour pour libération AM")
                continue

            if p.etat == 'INACTIF' and dispos:
                p.etat, p.temps_restant_etat = 'PRISE_POSTE', p.temps_prise
                p.h_debut_service_actuel = minute if p.vehicule_deja_affecte else (minute - p.temps_prise)
                p.enregistrer(minute, "PRISE_POSTE")
                continue

            if p.etat in ('DISPONIBLE', 'EN_RETOUR_DEPOT'):
                temps_trav = minute - p.h_debut_service_actuel
                dist_ret = matrice_travail.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                h_fin_poste = p.h_debut_service_actuel + p.amplitude_max
                h_limite_job = h_fin_poste - p.temps_passation  # dernière minute pour démarrer un job

                # ── Pause obligatoire ─────────────────────────────────────────
                besoin_p = (temps_trav >= p.amplitude_max // 2 and not p.pause_faite)
                if besoin_p:
                    nb_Jobs = max(math.ceil(prio_tension * len(dispos)), 1)
                    best_sj = selectionner_meilleur_job_retour(p, dispos, minute, matrice_travail, nb_Jobs, jobs_restants, est_premier_job=(p.couloir_actuel is None))
                    # Compatibilité stricte : approche + job + retour dépôt <= h_limite_job
                    approche = matrice_travail.get(p.position_actuelle, {}).get(best_sj.points_depart[0], 0) if best_sj else 0
                    if best_sj and (minute + approche + best_sj.poids_total + dist_ret) <= h_limite_job:
                        affecter_job_avec_matrice(p, best_sj, jobs_restants, dispos, minute, matrice_travail)
                        continue
                    # Aucun job compatible : aller au dépôt pour faire la pause.
                    # Si déjà au dépôt : pause immédiate, pas de boucle EN_RETOUR_DEPOT.
                    if p.position_actuelle != p.stationnement_initial:
                        p.etat = 'EN_RETOUR_DEPOT'
                        p.temps_restant_etat = dist_ret
                        p.enregistrer(minute, "RETOUR_DEPOT", details="Retour Pause")
                    else:
                        # Déjà au dépôt : pause déclenchée directement
                        p.etat, p.temps_restant_etat, p.pause_faite = 'EN_PAUSE', p.duree_pause, True
                        p.enregistrer(minute, "EN_PAUSE", details=f"Durée: {p.duree_pause}min")
                    continue

                # ── Chercher un job compatible ────────────────────────────────
                # Condition stricte : minute + approche + durée_job + retour_dépôt <= h_limite_job
                dispos_poste = [j for j in dispos if job_visible(j, p)]
                best_sj = None
                if dispos_poste:
                    nb_Jobs = max(math.ceil(prio_tension * len(dispos_poste)), 1)
                    candidat = selectionner_meilleur_job(p, dispos_poste, minute, matrice_travail, nb_Jobs, jobs_restants, jobs_reserves=jobs_reserves)
                    if candidat:
                        approche = matrice_travail.get(p.position_actuelle, {}).get(candidat.points_depart[0], 0)
                        if (minute + approche + candidat.poids_total + dist_ret) <= h_limite_job:
                            best_sj = candidat

                if best_sj:
                    affecter_job_avec_matrice(p, best_sj, jobs_restants, dispos, minute, matrice_travail)
                    continue

                # ── Aucun job compatible ──────────────────────────────────────
                # Si pas au dépôt : lancer EN_RETOUR_DEPOT (interruptible à chaque minute)
                # Si au dépôt    : attendre jusqu'à h_limite_job puis déclencher fin de poste
                if p.position_actuelle != p.stationnement_initial:
                    p.etat = 'EN_RETOUR_DEPOT'
                    p.temps_restant_etat = dist_ret
                    p.enregistrer(minute, "RETOUR_DEPOT", details="Retour fin de poste")
                elif minute >= h_limite_job:
                    p.etat, p.temps_restant_etat = 'FIN_DE_SERVICE', p.temps_passation
                    p.enregistrer(minute, "PASSATION_FIN")
                # Sinon : on reste DISPONIBLE, réévaluation à la prochaine minute

        if not jobs_restants and all(p.etat in ['INACTIF', 'FIN_DE_SERVICE', 'OPTIMISATION_AM', 'INTER_JOB', 'EN_RETOUR_DEPOT'] for p in postes):
            # ── Vérification des deadlines avant de valider la solution ──────────
            # Un job "affecté" mais livré après sa deadline = solution invalide.
            # On reconstruit les heures réelles de livraison depuis l'historique.
            violations = []
            sj_index = {sj.super_job_id: sj for sj in liste_sj_type}
            for p in postes:
                for i, ev in enumerate(p.historique):
                    if ev['Activite'] != 'EN_MISSION' or ev['SJ_ID'] == 'N/A':
                        continue
                    sj = sj_index.get(ev['SJ_ID'])
                    if not sj:
                        continue
                    t_debut_mission = ev['Minute_Debut']
                    h_fin_livraison = t_debut_mission + sj.poids_total
                    deadline = to_min(sj.h_deadline_min)
                    if h_fin_livraison > deadline:
                        violations.append(sj.super_job_id)
            if violations:
                # Des deadlines sont violées → solution invalide
                return None, violations  # on retourne les sj_ids en violation comme "non traités"
            return postes, []
        minute += 1
    return None, jobs_restants

def affecter_job_avec_matrice(p, sj, jobs_restants, dispos, minute, matrice_travail):
    p.job_en_cours = sj
    jobs_restants.remove(sj)
    if sj in dispos: dispos.remove(sj)
    p.etat, p.temps_restant_etat = 'EN_TRAJET_VIDE', matrice_travail.get(p.position_actuelle, {}).get(sj.points_depart[0], 0)
    p.enregistrer(minute, "EN_TRAJET_VIDE", sj, "Approche Mission")

def selectionner_meilleur_job_retour(p, dispos, minute, matrice_duree, nb_Jobs, jobs_restants, est_premier_job=False, limite_critique=270):
    zone_depot = p.stationnement_initial[:3]
    candidats_v = [sj for sj in dispos if sj.points_arrivee[-1][:3] == zone_depot and ((minute + matrice_duree.get(p.position_actuelle, {}).get(sj.points_depart[0], 20) + sj.poids_total + matrice_duree.get(sj.points_arrivee[-1], {}).get(p.stationnement_initial, 20)) - p.h_debut_service_actuel) <= limite_critique]
    return selectionner_meilleur_job(p, candidats_v, minute, matrice_duree, nb_Jobs, jobs_restants, est_premier_job) if candidats_v else None  # pas de réservations pour selectionner_meilleur_job_retour

# =================================================================
# 4. FONCTION D'ENTRÉE PRINCIPALE (OPTIMISÉE)
# =================================================================

def trouver_meilleure_configuration_journee(liste_sj, n_max_dict, df_vehicules, matrice_duree, params_logistique):
    postes_complets = []
    tensions_test = [0.2, 0.4, 0.6, 0.8, 1.0]
    
    for v_type, val_max in n_max_dict.items():
        pic_charge = max(val_max) if isinstance(val_max, list) else val_max
        n_depart, n_limite = max(1, math.floor(pic_charge * 0.5)), math.ceil(pic_charge * 2.5)
        jobs_v = [sj for sj in liste_sj if sj.v_type == v_type]
        if not jobs_v: continue
            
        meilleure_sol = None
        min_im = float('inf')
        min_iam = float('inf')
        max_occ = -1

        st.write(f"🔍 **{v_type}** | {len(jobs_v)} SJ | pic={pic_charge:.1f} | n=[{n_depart}..{min(n_limite,len(jobs_v))}]")

        for tension in tensions_test:
            for im in range(n_depart, min(n_limite, len(jobs_v)) + 1):
                if im > min_im: break

                for iam in range(1, im + 1):
                    if im == min_im and iam > min_iam: break

                    res, jobs_nt = simuler_faisabilite(im, iam, tension, jobs_v, v_type, matrice_duree, params_logistique, df_vehicules)

                    if res is not None and len(jobs_nt) > 0:
                        # jobs_nt peut contenir des SJ non traités OU des SJ_IDs en violation de deadline
                        if isinstance(jobs_nt[0], str):
                            st.write(f"  ⚠️ im={im} iam={iam} t={tension:.1f} → {len(jobs_nt)} deadline(s) violée(s) — rejetée")
                        else:
                            st.write(f"  ⚠️ im={im} iam={iam} t={tension:.1f} → {len(jobs_v)-len(jobs_nt)}/{len(jobs_v)} traités — rejetée")
                        res = None
                    elif res is None:
                        st.write(f"  ❌ im={im} iam={iam} t={tension:.1f} → aucune solution")
                    else:
                        st.write(f"  ✅ im={im} iam={iam} t={tension:.1f} → complète !")

                    if res:
                        # Calcul de la performance de cette solution
                        trav_utile, ampl_conso = 0, 0
                        for p in res:
                            if p.historique:
                                ampl_conso += (p.historique[-1]['Minute_Debut'] - p.historique[0]['Minute_Debut'])
                                for h in p.historique:
                                    if h['Activite'] == 'EN_MISSION': 
                                        trav_utile += h.get('sj_poids', 0)
                                    elif any(x in h['Activite'] for x in ['TRAJET_VIDE', 'RETOUR', 'INTERMISSION']): 
                                        trav_utile += 15 # Valorisation du temps de trajet/attente
                        
                        taux_occ = trav_utile / max(ampl_conso, 1)

                        # LOGIQUE DE DÉCISION HIÉRARCHIQUE :
                        # 1. Est-ce que le nombre de véhicules (im) est meilleur ?
                        if im < min_im:
                            min_im, min_iam, max_occ, meilleure_sol = im, iam, taux_occ, res
                        
                        # 2. Si im identique, est-ce que le nombre de postes (iam) est meilleur ?
                        elif im == min_im:
                            if iam < min_iam:
                                min_iam, max_occ, meilleure_sol = iam, taux_occ, res
                            
                            # 3. Si im et iam identiques, est-ce que le taux d'occupation est meilleur ?
                            elif iam == min_iam:
                                if taux_occ > max_occ:
                                    max_occ, meilleure_sol = taux_occ, res
                        
                        # On a trouvé une solution pour ce couple (im, iam), 
                        # on passe à la tension suivante ou on break selon besoin.
                        # Ici on break iam car on cherche le iam MIN pour ce im.
                        break 

        if meilleure_sol:
            st.success(f"✅ **{v_type}** : Optimisé (Im:{min_im}, Iam:{min_iam}, Occ:{max_occ:.1%})")
            postes_complets.extend(meilleure_sol)
        else:
            st.error(f"❌ **{v_type}** : Échec de planification.")

    return {"succes": len(postes_complets) > 0, "postes": postes_complets}




def afficher_controle_coherence(liste_globale_sj, postes_complets):
    """
    Compare les flux demandés vs les flux réalisés par type de contenant.
    """
    st.subheader("Validator : Contrôle de cohérence des flux")
    
    # 1. Calcul du Théorique (ce qui était dans la liste de départ)
    flux_theorique = {}
    for sj in liste_globale_sj:
        for job in sj.liste_jobs:
            c_type = getattr(job, 'contenant', 'Inconnu')
            flux_theorique[c_type] = flux_theorique.get(c_type, 0) + 1

    # 2. Calcul du Réel (ce qui est présent dans l'historique des postes)
    flux_reel = {c: 0 for c in flux_theorique.keys()}
    jobs_vus = set() # Pour éviter les doublons si un historique est mal lu
    
    for p in postes_complets:
        for h in p.historique:
            if h['Activite'] == 'EN_MISSION' and h['SJ_ID'] != "N/A":
                # On retrouve le SuperJob original pour compter ses jobs internes
                sj_id = h['SJ_ID']
                if sj_id not in jobs_vus:
                    # On cherche le SJ dans la liste globale pour avoir le détail des contenants
                    target_sj = next((s for s in liste_globale_sj if s.super_job_id == sj_id), None)
                    if target_sj:
                        for j_interne in target_sj.liste_jobs:
                            c_type = getattr(j_interne, 'contenant', 'Inconnu')
                            flux_reel[c_type] = flux_reel.get(c_type, 0) + 1
                        jobs_vus.add(sj_id)

    # 3. Construction de la Matrice (Tableau)
    donnees_controle = []
    total_theorique = 0
    total_reel = 0

    for contenant in sorted(flux_theorique.keys()):
        theo = flux_theorique[contenant]
        reel = flux_reel.get(contenant, 0)
        status = "✅" if theo == reel else "❌"
        
        donnees_controle.append({
            "Type de Contenant": contenant,
            "Flux Théoriques": theo,
            "Flux Réalisés": reel,
            "Statut": status
        })
        total_theorique += theo
        total_reel += reel

    # Affichage dans Streamlit
    df_controle = pd.DataFrame(donnees_controle)
    
    # Ajout d'une ligne de total pour la visibilité globale
    st.table(df_controle)
    
    if total_theorique == total_reel:
        st.success(f"Cohérence parfaite : {total_reel}/{total_theorique} missions effectuées.")
    else:
        diff = total_theorique - total_reel
        st.error(f"Attention : {diff} missions n'ont pas été planifiées !")
