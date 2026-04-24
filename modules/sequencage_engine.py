import pandas as pd
import math
import streamlit as st
from datetime import time, datetime, timedelta

# =================================================================
# 1. UTILITAIRES
# =================================================================

def to_min(t):
    if isinstance(t, (time, datetime)):
        return t.hour * 60 + t.minute
    return float(t)

def get_couloir_id(sj):
    if not sj.points_depart or not sj.points_arrivee:
        return "INCONNU"
    pts = sorted([sj.points_depart[0], sj.points_arrivee[-1]])
    return f"{pts[0]}--{pts[1]}"

def calculer_stress_dynamique(sj, minute_actuelle):
    """Stress index basé sur la deadline du premier maillon."""
    h_deadline_critique = to_min(sj.liste_jobs[0].h_deadline)
    temps_restant = h_deadline_critique - minute_actuelle
    if temps_restant <= 0: return 9999.0
    
    # On compare la durée du premier trajet (estimée) au temps restant
    ratio = sj.poids_total / (temps_restant + 1)
    return ratio * (1 / max(0.01, (1.1 - ratio)))

# =================================================================
# 2. LOGIQUE DE SÉLECTION (MAILLON CRITIQUE)
# =================================================================

def selectionner_meilleur_job(p, dispos, minute, matrice_duree):
    candidats_possibles = []
    
    for j in dispos:
        first_job = j.liste_jobs[0]
        h_deadline_1er = to_min(first_job.h_deadline)
        
        # 1. Temps d'approche (Dépôt/Position -> Origine 1er Job)
        dist_approche = matrice_duree.get(p.position_actuelle, {}).get(j.points_depart[0], 0)
        
        # 2. Temps de mission du 1er Job (Chargement + Trajet + Déchargement)
        # On l'estime ici par le poids du premier job ou une fraction du poids total
        duree_1er_job = first_job.poids if hasattr(first_job, 'poids') else (j.poids_total / len(j.liste_jobs))
        
        # CONDITION CRITIQUE : Fin de livraison du 1er job <= Deadline 1er job
        if minute + dist_approche + duree_1er_job <= h_deadline_1er:
            j.stress_temp = calculer_stress_dynamique(j, minute)
            candidats_possibles.append(j)

    if not candidats_possibles:
        return None

    candidats_possibles.sort(key=lambda x: x.stress_temp, reverse=True)
    top_candidates = candidats_possibles[:3]

    # Priorités : Couloir > Sur place > Proximité
    for j in top_candidates:
        if get_couloir_id(j) == p.couloir_actuel and j.points_depart[0] == p.position_actuelle:
            return j
    for j in top_candidates:
        if j.points_depart[0] == p.position_actuelle:
            return j
            
    top_candidates.sort(key=lambda x: matrice_duree.get(p.position_actuelle, {}).get(x.points_depart[0], 999))
    return top_candidates[0]

# =================================================================
# 3. MOTEUR DE SIMULATION
# =================================================================

def simuler_faisabilite(I, liste_sj_type, v_type, matrice_duree, params_logistique, df_vehicules):
    rh = params_logistique.get('rh', {})
    h_start = to_min(rh.get('h_prise_min', 360))
    h_end = to_min(rh.get('h_fin_max', 1260))
    pas = 5
    
    try:
        depot_initial = df_vehicules[df_vehicules['Types'] == v_type]['Stationnement initial'].iloc[0]
    except:
        depot_initial = "DEPOT"

    from modules.sequencage_engine import PosteChauffeur # Import local si nécessaire
    postes = [PosteChauffeur(f"{v_type}_{i+1}", v_type, depot_initial, rh) for i in range(I)]
    jobs_restants = list(liste_sj_type)
    minute = h_start

    while minute <= h_end:
        for p in postes:
            if p.temps_restant_etat > 0:
                p.temps_restant_etat -= pas
                continue
            
            if p.etat == 'PRISE_POSTE': p.etat = 'DISPONIBLE'
            elif p.etat == 'EN_TRAJET_VIDE':
                p.position_actuelle = p.job_en_cours.points_depart[0]
                p.etat = 'EN_MISSION'
                p.temps_restant_etat = p.job_en_cours.poids_total
                p.enregistrer(minute, "EN_MISSION", p.job_en_cours)
            elif p.etat == 'EN_MISSION':
                # On valide que la livraison du 1er job a bien été faite à temps (rétro-actif)
                # Si on est ici, c'est que la mission est finie.
                p.position_actuelle = p.job_en_cours.points_arrivee[-1]
                p.couloir_actuel = get_couloir_id(p.job_en_cours)
                p.etat = 'DISPONIBLE'
                p.job_en_cours = None
            elif p.etat == 'RETOUR_DEPOT':
                p.position_actuelle = p.stationnement_initial
                p.etat = 'EN_PAUSE'; p.temps_restant_etat = p.duree_pause
                p.pause_faite = True; p.enregistrer(minute, "EN_PAUSE")
            elif p.etat == 'EN_PAUSE': p.etat = 'DISPONIBLE'

        # Affectation
        dispos = [j for j in jobs_restants if to_min(j.liste_jobs[0].h_dispo) <= minute]
        for p in postes:
            if p.etat == 'INACTIF' and dispos:
                p.etat = 'PRISE_POSTE'; p.temps_restant_etat = 15
                p.h_debut_service = minute; p.enregistrer(minute, "PRISE_POSTE")
            
            elif p.etat == 'DISPONIBLE':
                # Amplitude et Pause
                if (minute - (p.h_debut_service or minute)) >= (p.amplitude_max / 2) and not p.pause_faite:
                    if p.position_actuelle == p.stationnement_initial:
                        p.etat = 'EN_PAUSE'; p.temps_restant_etat = p.duree_pause; p.pause_faite = True
                        p.enregistrer(minute, "EN_PAUSE")
                    else:
                        dist = matrice_duree.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                        p.etat = 'RETOUR_DEPOT'; p.temps_restant_etat = dist
                        p.enregistrer(minute, "EN_TRAJET_VIDE", details="Retour Dépôt (Pause)")
                    continue

                if not dispos: continue
                
                sj_choisi = selectionner_meilleur_job(p, dispos, minute, matrice_duree)
                if sj_choisi:
                    dist = matrice_duree.get(p.position_actuelle, {}).get(sj_choisi.points_depart[0], 0)
                    
                    p.job_en_cours = sj_choisi
                    jobs_restants.remove(sj_choisi)
                    dispos.remove(sj_choisi)
                    
                    if dist > 0:
                        p.etat = 'EN_TRAJET_VIDE'; p.temps_restant_etat = dist
                        p.enregistrer(minute, "EN_TRAJET_VIDE", sj_choisi)
                    else:
                        p.etat = 'EN_MISSION'; p.temps_restant_etat = sj_choisi.poids_total
                        p.enregistrer(minute, "EN_MISSION", sj_choisi)

        if not jobs_restants: return postes
        minute += pas
    return None

# =================================================================
# 4. FONCTION D'ENTREE
# =================================================================

def trouver_meilleure_configuration_journee(liste_sj, n_max_dict, df_vehicules, matrice_duree, params_logistique):
    postes_complets = []
    for v_type, val_max in n_max_dict.items():
        n_max_calc = math.ceil(max(val_max) * 1.20) if isinstance(val_max, list) else math.ceil(val_max * 1.20)
        st.info(f"Analyse **{v_type}** : Max autorisé {n_max_calc} camions.")
        
        jobs_v = [sj for sj in liste_sj if sj.v_type == v_type]
        if not jobs_v: continue
            
        solution_trouvee = False
        for I in range(1, n_max_calc + 1):
            resultat = simuler_faisabilite(I, jobs_v, v_type, matrice_duree, params_logistique, df_vehicules)
            if resultat:
                st.success(f"✅ **{v_type}** : Solution validée avec **{I}** véhicule(s).")
                postes_complets.extend(resultat)
                solution_trouvee = True
                break
        
        if not solution_trouvee:
            st.error(f"❌ **{v_type}** : Aucun planning valide (contraintes horaires bloquantes).")

    return {"succes": len(postes_complets) > 0, "postes": postes_complets}
