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
        
    def enregistrer(self, minute, activite, sj=None, details=""):
        sj_id = sj.super_job_id if sj else "N/A"
        sj_poids = sj.poids_total if sj else 0
        self.historique.append({
            "Minute_Debut": minute,
            "Heure_Debut": f"{int(minute//60):02d}h{int(minute%60):02d}",
            "Activite": activite,
            "SJ_ID": sj_id,
            "sj_poids": sj_poids,
            "Details": details
        })

# =================================================================
# 3. MOTEUR DE SIMULATION
# =================================================================

def selectionner_meilleur_job(p, dispos, minute, matrice_duree, nb_Jobs, jobs_restants, est_premier_job=False):
    if not dispos: return None
    liste_candidats = []
    for sj in dispos:
        stress = calculer_stress_maillon_critique(sj, minute, matrice_duree, p.position_actuelle)
        liste_candidats.append({'sj': sj, 'stress': stress})
    
    liste_candidats.sort(key=lambda x: x['stress'], reverse=True)
    top_n_jobs = [item['sj'] for item in liste_candidats[:nb_Jobs]]
    
    couloir_precedent = p.couloir_actuel
    for sj in top_n_jobs:
        if couloir_precedent and get_couloir_id(sj) == couloir_precedent and sj.points_depart[0] == p.position_actuelle:
            return sj
    for sj in top_n_jobs:
        if sj.points_depart[0] == p.position_actuelle: return sj
    for sj in top_n_jobs:
        if sj.points_depart[0][:3] == p.position_actuelle[:3]: return sj
            
    best_sj_proximite, dist_min = None, float('inf')
    for sj in top_n_jobs:
        dist = matrice_duree.get(p.position_actuelle, {}).get(sj.points_depart[0], 0)
        if dist < dist_min:
            dist_min, best_sj_proximite = dist, sj
    return best_sj_proximite

def simuler_faisabilite(I_matin, I_am, prio_tension, liste_sj_type, v_type, matrice_duree, params_logistique, df_vehicules):
    rh = params_logistique.get('rh', {})
    h_start = to_min(rh.get('h_prise_min', 360))
    h_end = to_min(rh.get('h_fin_max', 1380))
    h_bascule = h_start + to_min(rh.get('amplitude_totale', 450)) - 100
    
    facteur_alea = 1 + (params_logistique.get('alea_circulation', 0) / 100)
    matrice_travail = {o: {d: dur * facteur_alea for d, dur in dests.items()} for o, dests in matrice_duree.items()}
    
    filtre = df_vehicules[df_vehicules['Types'] == v_type]
    depot_initial = filtre['Stationnement initial'].iloc[0] if not filtre.empty else "HSJ"

    postes = [PosteChauffeur(f"{v_type}_{i+1}", v_type, depot_initial, rh) for i in range(I_matin)]
    jobs_restants = list(liste_sj_type)
    minute = h_start

    while minute <= h_end:
        for p in postes:
            if p.etat == 'OPTIMISATION_AM': continue
            if p.temps_restant_etat > 0:
                p.temps_restant_etat -= 1
                continue
            
            if p.etat == 'PRISE_POSTE':
                p.etat, p.vehicule_deja_affecte = 'DISPONIBLE', True
            elif p.etat == 'EN_TRAJET_VIDE':
                if p.job_en_cours:
                    p.position_actuelle = p.job_en_cours.points_depart[0]
                    p.etat, p.temps_restant_etat = 'EN_MISSION', p.job_en_cours.poids_total
                    p.enregistrer(minute, "EN_MISSION", p.job_en_cours)
                else:
                    p.position_actuelle = p.stationnement_initial
                    idx_p = int(p.id_poste.split('_')[-1])
                    if minute >= h_bascule and idx_p > I_am:
                        p.etat, p.temps_restant_etat = 'OPTIMISATION_AM', 9999
                        p.enregistrer(minute, "VEHICULE_LIBERE", details="Désengagement (Optimisation AM)")
                        continue
                    temps_trav = minute - p.h_debut_service_actuel
                    if temps_trav >= p.amplitude_max - p.temps_passation:
                        p.etat, p.temps_restant_etat = 'FIN_DE_SERVICE', p.temps_passation
                        p.enregistrer(minute, "PASSATION_FIN")
                    elif not p.pause_faite:
                        p.etat, p.temps_restant_etat, p.pause_faite = 'EN_PAUSE', p.duree_pause, True
                        p.enregistrer(minute, "EN_PAUSE", details=f"Durée: {p.duree_pause}min")
                    else: p.etat = 'DISPONIBLE'
            elif p.etat == 'EN_MISSION':
                p.position_actuelle, p.couloir_actuel, p.etat, p.job_en_cours = p.job_en_cours.points_arrivee[-1], get_couloir_id(p.job_en_cours), 'DISPONIBLE', None
            elif p.etat == 'EN_PAUSE': p.etat = 'DISPONIBLE'
            elif p.etat == 'FIN_DE_SERVICE' and p.temps_restant_etat == 0 :
                p.etat, p.h_debut_service_actuel, p.pause_faite, p.couloir_actuel = 'INACTIF', None, False, None
                p.enregistrer(minute, "VEHICULE_LIBERE")

        dispos = [j for j in jobs_restants if to_min(j.h_dispo_min) <= minute]
        for p in postes:
            if p.etat == 'OPTIMISATION_AM' or p.temps_restant_etat > 0: continue
            
            idx_p = int(p.id_poste.split('_')[-1])
            if p.etat == 'DISPONIBLE' and minute >= h_bascule and idx_p > I_am:
                if p.position_actuelle == p.stationnement_initial:
                    p.etat, p.temps_restant_etat = 'OPTIMISATION_AM', 9999
                    p.enregistrer(minute, "VEHICULE_LIBERE", details="Désengagement (Optimisation AM)")
                else:
                    p.etat, p.temps_restant_etat = 'EN_TRAJET_VIDE', matrice_travail.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                    p.enregistrer(minute, "RETOUR_DEPOT", details="Retour pour libération AM")
                continue

            if p.etat == 'INACTIF' and dispos:
                p.etat, p.temps_restant_etat = 'PRISE_POSTE', p.temps_prise
                p.h_debut_service_actuel = minute if p.vehicule_deja_affecte else (minute - p.temps_prise)
                p.enregistrer(minute, "PRISE_POSTE")
                continue

            if p.etat == 'DISPONIBLE':
                temps_trav = minute - p.h_debut_service_actuel
                dist_ret = matrice_travail.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                besoin_p, besoin_f = (temps_trav >= 70 and not p.pause_faite), (temps_trav >= p.amplitude_max - params_logistique.get('duree_max_superjob', 60))
                
                if besoin_f or besoin_p:
                    nb_Jobs = max(math.ceil(prio_tension * len(dispos)), 1)
                    best_sj = selectionner_meilleur_job_retour(p, dispos, minute, matrice_travail, nb_Jobs, jobs_restants, est_premier_job=(p.couloir_actuel is None))
                    if best_sj and (minute + best_sj.poids_total + dist_ret) <= (p.h_debut_service_actuel + p.amplitude_max - p.temps_passation):
                        affecter_job_avec_matrice(p, best_sj, jobs_restants, dispos, minute, matrice_travail)
                        continue
                    if p.position_actuelle != p.stationnement_initial:
                        p.etat, p.temps_restant_etat = 'EN_TRAJET_VIDE', dist_ret
                        p.enregistrer(minute, "RETOUR_DEPOT", details="Force Impératif")
                    else:
                        p.etat, p.temps_restant_etat = ('FIN_DE_SERVICE' if besoin_f else 'EN_PAUSE'), (p.temps_passation if besoin_f else p.duree_pause)
                        if not besoin_f: p.pause_faite = True
                    continue
                elif dispos:
                    nb_Jobs = max(math.ceil(prio_tension * len(dispos)), 1)
                    best_sj = selectionner_meilleur_job(p, dispos, minute, matrice_travail, nb_Jobs, jobs_restants)
                    if best_sj and (minute + best_sj.poids_total + dist_ret) <= (p.h_debut_service_actuel + p.amplitude_max):
                        affecter_job_avec_matrice(p, best_sj, jobs_restants, dispos, minute, matrice_travail)

        if not jobs_restants and all(p.etat in ['INACTIF', 'FIN_DE_SERVICE', 'OPTIMISATION_AM'] for p in postes): return postes
        minute += 1
    return None

def affecter_job_avec_matrice(p, sj, jobs_restants, dispos, minute, matrice_travail):
    p.job_en_cours = sj
    jobs_restants.remove(sj)
    if sj in dispos: dispos.remove(sj)
    p.etat, p.temps_restant_etat = 'EN_TRAJET_VIDE', matrice_travail.get(p.position_actuelle, {}).get(sj.points_depart[0], 0)
    p.enregistrer(minute, "EN_TRAJET_VIDE", sj, "Approche Mission")

def selectionner_meilleur_job_retour(p, dispos, minute, matrice_duree, nb_Jobs, jobs_restants, est_premier_job=False, limite_critique=270):
    zone_depot = p.stationnement_initial[:3]
    candidats_v = [sj for sj in dispos if sj.points_arrivee[-1][:3] == zone_depot and ((minute + matrice_duree.get(p.position_actuelle, {}).get(sj.points_depart[0], 20) + sj.poids_total + matrice_duree.get(sj.points_arrivee[-1], {}).get(p.stationnement_initial, 20)) - p.h_debut_service_actuel) <= limite_critique]
    return selectionner_meilleur_job(p, candidats_v, minute, matrice_duree, nb_Jobs, jobs_restants, est_premier_job) if candidats_v else None

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
            
        meilleure_sol, min_v_total, max_occ = None, float('inf'), -1

        for tension in tensions_test:
            for im in range(n_depart, n_limite + 1):
                if im > min_v_total: break
                for iam in range(1, im + 1):
                    res = simuler_faisabilite(im, iam, tension, jobs_v, v_type, matrice_duree, params_logistique, df_vehicules)
                    if res:
                        trav_utile, ampl_conso = 0, 0
                        for p in res:
                            if p.historique:
                                ampl_conso += (p.historique[-1]['Minute_Debut'] - p.historique[0]['Minute_Debut'])
                                for h in p.historique:
                                    if h['Activite'] == 'EN_MISSION': trav_utile += h['sj_poids']
                                    elif 'TRAJET_VIDE' in h['Activite'] or 'RETOUR' in h['Activite']: trav_utile += 15
                        
                        taux_occ = trav_utile / max(ampl_conso, 1)
                        if (im + iam) < min_v_total or ((im + iam) == min_v_total and taux_occ > max_occ):
                            min_v_total, max_occ, meilleure_sol = (im + iam), taux_occ, res
                        break
                if meilleure_sol and (im + 1) > min_v_total: break

        if meilleure_sol:
            st.success(f"✅ **{v_type}** : Optimisé avec Taux Occ: {max_occ:.1%}")
            postes_complets.extend(meilleure_sol)
        else: st.error(f"❌ **{v_type}** : Échec.")

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
