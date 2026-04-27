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
        poids = sj.poids_total if sj else 0
        self.historique.append({
            "Minute_Debut": minute,
            "Heure_Debut": f"{int(minute//60):02d}h{int(minute%60):02d}",
            "Activite": activite,
            "SJ_ID": sj_id,
            "sj_poids": poids,
            "Details": details
        })

# =================================================================
# 3. MOTEUR DE SIMULATION
# =================================================================

def selectionner_meilleur_job(p, dispos, minute, matrice_duree, nb_Jobs, jobs_restants):
    if not dispos: return None
    liste_candidats = []
    for sj in dispos:
        stress = calculer_stress_maillon_critique(sj, minute, matrice_duree, p.position_actuelle)
        liste_candidats.append({'sj': sj, 'stress': stress})
    
    liste_candidats.sort(key=lambda x: x['stress'], reverse=True)
    top_n_jobs = [item['sj'] for item in liste_candidats[:nb_Jobs]]
    
    for sj in top_n_jobs:
        if p.couloir_actuel and get_couloir_id(sj) == p.couloir_actuel and sj.points_depart[0] == p.position_actuelle:
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
    h_end_global = to_min(rh.get('h_fin_max', 1380))
    h_bascule = h_start + to_min(rh.get('amplitude_totale', 450)) - 1
    marge_interjob = params_logistique.get('marge_interjob', 0)
    
    facteur_alea = 1 + (params_logistique.get('alea_circulation', 0) / 100)
    matrice_travail = {o: {d: dur * facteur_alea for d, dur in dests.items()} for o, dests in matrice_duree.items()}
    
    filtre = df_vehicules[df_vehicules['Types'] == v_type]
    depot_initial = filtre['Stationnement initial'].iloc[0] if not filtre.empty else "HSJ"

    postes = [PosteChauffeur(f"{v_type}_{i+1}", v_type, depot_initial, rh) for i in range(I_matin)]
    jobs_restants = list(liste_sj_type)
    minute = h_start

    while minute <= h_end_global:
        tous_liberes = True
        for p in postes:
            if p.etat == 'OPTIMISATION_AM': continue
            tous_liberes = False
            
            if p.temps_restant_etat > 0:
                p.temps_restant_etat -= 1
                continue
            
            # --- TRANSITIONS ---
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
                        p.etat, p.temps_restant_etat = 'PASSATION_AM', p.temps_passation
                        p.enregistrer(minute, "PASSATION_FIN", details="Transition AM")
                    else: p.etat = 'DISPONIBLE'
            elif p.etat == 'EN_MISSION':
                p.position_actuelle, p.couloir_actuel, p.job_en_cours = p.job_en_cours.points_arrivee[-1], get_couloir_id(p.job_en_cours), None
                if marge_interjob > 0:
                    p.etat, p.temps_restant_etat = 'INTERMISSION', marge_interjob
                    p.enregistrer(minute, "INTERMISSION", details=f"Attente fixe {marge_interjob}min")
                else: p.etat = 'DISPONIBLE'
            elif p.etat == 'INTERMISSION': p.etat = 'DISPONIBLE'
            elif p.etat == 'PASSATION_AM':
                p.etat = 'OPTIMISATION_AM'
                p.enregistrer(minute, "VEHICULE_LIBERE", details="Fin service AM")
            elif p.etat == 'FIN_DE_SERVICE':
                p.etat, p.h_debut_service_actuel = 'INACTIF', None
                p.enregistrer(minute, "FIN_DE_SERVICE")

            # --- AFFECTATION ---
            if p.etat == 'DISPONIBLE':
                idx_p = int(p.id_poste.split('_')[-1])
                if minute >= h_bascule and idx_p > I_am:
                    if p.position_actuelle == p.stationnement_initial:
                        p.etat, p.temps_restant_etat = 'PASSATION_AM', p.temps_passation
                        p.enregistrer(minute, "PASSATION_FIN", details="Transition AM")
                    else:
                        p.etat, p.temps_restant_etat = 'EN_TRAJET_VIDE', matrice_travail.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                        p.enregistrer(minute, "RETOUR_DEPOT", details="Libération AM")
                    continue

                dispos = [j for j in jobs_restants if to_min(j.h_dispo_min) <= minute]
                if dispos:
                    nb_Jobs = max(math.ceil(prio_tension * len(dispos)), 1)
                    best_sj = selectionner_meilleur_job(p, dispos, minute, matrice_travail, nb_Jobs, jobs_restants)
                    if best_sj:
                        dist_ret = matrice_travail.get(best_sj.points_arrivee[-1], {}).get(p.stationnement_initial, 30)
                        if (minute + best_sj.poids_total + dist_ret) <= (p.h_debut_service_actuel + p.amplitude_max):
                            p.job_en_cours = best_sj
                            jobs_restants.remove(best_sj)
                            p.etat, p.temps_restant_etat = 'EN_TRAJET_VIDE', matrice_travail.get(p.position_actuelle, {}).get(best_sj.points_depart[0], 0)
                            p.enregistrer(minute, "EN_TRAJET_VIDE", best_sj, "Approche Mission")
                            continue
                
                # Remplissage si fin de journée
                if (minute - p.h_debut_service_actuel) >= p.amplitude_max - p.temps_passation:
                    if p.position_actuelle != p.stationnement_initial:
                        p.etat, p.temps_restant_etat = 'EN_TRAJET_VIDE', matrice_travail.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                        p.enregistrer(minute, "RETOUR_DEPOT")
                    else:
                        p.etat, p.temps_restant_etat = 'FIN_DE_SERVICE', p.temps_passation
                        p.enregistrer(minute, "PASSATION_FIN")

            if p.etat == 'INACTIF' and jobs_restants:
                p.etat, p.temps_restant_etat, p.h_debut_service_actuel = 'PRISE_POSTE', p.temps_prise, minute
                p.enregistrer(minute, "PRISE_POSTE")

        if tous_liberes and not jobs_restants: break
        minute += 1
    return postes

# =================================================================
# 4. OPTIMISATION MULTI-TENSIONS
# =================================================================

def trouver_meilleure_configuration_journee(liste_sj, n_max_dict, df_vehicules, matrice_duree, params_logistique):
    postes_complets = []
    tensions = [0.2, 0.4, 0.6, 0.8, 1.0]
    
    for v_type, val_max in n_max_dict.items():
        pic = max(val_max) if isinstance(val_max, list) else val_max
        n_dep, n_lim = max(1, math.floor(pic * 0.5)), math.ceil(pic * 2.5)
        jobs_v = [sj for sj in liste_sj if sj.v_type == v_type]
        if not jobs_v: continue
            
        meilleure_sol, min_v, max_occ = None, float('inf'), -1

        for tension in tensions:
            for im in range(n_dep, n_lim + 1):
                if im > min_v: break
                for iam in range(1, im + 1):
                    res = simuler_faisabilite(im, iam, tension, jobs_v, v_type, matrice_duree, params_logistique, df_vehicules)
                    if res:
                        trav_utile, ampl_conso = 0, 0
                        for p in res:
                            if p.historique:
                                ampl_conso += (p.historique[-1]['Minute_Debut'] - p.historique[0]['Minute_Debut'])
                                for h in p.historique:
                                    if h['Activite'] == 'EN_MISSION': trav_utile += h.get('sj_poids', 0)
                                    elif any(x in h['Activite'] for x in ['TRAJET_VIDE', 'RETOUR', 'INTERMISSION']): trav_utile += 10
                        
                        taux_occ = trav_utile / max(ampl_conso, 1)
                        if (im + iam) < min_v or ((im + iam) == min_v and taux_occ > max_occ):
                            min_v, max_occ, meilleure_sol = (im + iam), taux_occ, res
                        break
                if meilleure_sol and (im + 1) > min_v: break

        if meilleure_sol:
            st.success(f"✅ **{v_type}** optimisé (Taux Occ: {max_occ:.1%})")
            postes_complets.extend(meilleure_sol)
        else: st.error(f"❌ **{v_type}** : Échec.")

    return {"succes": len(postes_complets) > 0, "postes": postes_complets}
