import pandas as pd
import math
from datetime import time, datetime

# =================================================================
# 1. UTILITAIRES
# =================================================================

def to_decimal_minutes(t):
    if isinstance(t, (time, datetime)):
        return t.hour * 60 + t.minute
    return 0

def sont_dans_le_meme_couloir(sj1, sj2):
    """
    Vérifie si deux SuperJobs appartiennent au même axe (Aller ou Retour).
    Basé sur les attributs .origin et .destination des jobs contenus.
    """
    if not sj1 or not sj2:
        return False
    
    # Extraction des points de passage du SuperJob
    o1, d1 = sj1.origin, sj1.destination
    o2, d2 = sj2.origin, sj2.destination
    
    condition_aller = (o1 == o2 and d1 == d2)
    condition_retour = (o1 == d2 and d1 == o2)
    
    return condition_aller or condition_retour

# =================================================================
# 2. CLASSE POSTE CHAUFFEUR
# =================================================================

class PosteChauffeur:
    def __init__(self, id_poste, vehicule_type, site_initial, params_rh):
        self.id_poste = id_poste
        self.vehicule_type = vehicule_type
        self.stationnement_initial = site_initial
        self.position_actuelle = site_initial
        
        self.etat = 'INACTIF'
        self.job_en_cours = None
        self.job_precedent = None  # Mémoire pour le couloir dynamique
        
        self.temps_restant_etat = 0
        self.temps_service_total = 0   
        self.is_pause_faite = False
        
        self.amplitude_max = params_rh.get('v_duree', 450)
        self.duree_pause = params_rh.get('v_pause', 45)
        self.t_manoeuvre = 10 
        self.historique = []

    def enregistrer_evenement(self, minute_actuelle, activite, sj=None, details=""):
        destination_visu = self.position_actuelle
        sj_id = "N/A"
        if sj:
            sj_id = getattr(sj, 'flux_id', 'SJ')
            if activite in ['EN_TRAJET_PLEIN', 'EN_DECHARGEMENT']:
                destination_visu = sj.destination
            elif activite in ['EN_TRAJET_VIDE', 'EN_MANOEUVRE_QUAI']:
                destination_visu = sj.origin

        self.historique.append({
            "Poste": self.id_poste,
            "Type": self.vehicule_type,
            "Minute_Debut": minute_actuelle,
            "Heure_Debut": f"{int(minute_actuelle//60):02d}:{int(minute_actuelle%60):02d}",
            "Activite": activite,
            "Origine": self.position_actuelle,
            "Destination": destination_visu,
            "SJ_ID": sj_id,
            "Details": details
        })

    def mettre_a_jour(self, pas_temps):
        if self.etat in ['INACTIF', 'FIN_POSTE']: return False
        self.temps_service_total += pas_temps
        if self.temps_restant_etat > 0:
            self.temps_restant_etat -= pas_temps
            return self.temps_restant_etat <= 0
        return True

    def verifier_besoin_pause(self):
        return not self.is_pause_faite and self.temps_service_total >= (self.amplitude_max / 2)

    def verifier_fin_service(self):
        return self.temps_service_total >= self.amplitude_max

    def est_disponible(self):
        return self.etat == 'DISPONIBLE' and self.temps_restant_etat == 0

# =================================================================
# 3. LOGIQUE DE SELECTION (SUPERJOBS)
# =================================================================

def calculer_score_stress(sj, temps_actuel):
    """Utilise h_deadline_min et poids_total de la classe SuperJob"""
    temps_restant = sj.h_deadline_min - temps_actuel
    if temps_restant <= 0: return 999999
    
    ratio = sj.poids_total / temps_restant
    return ratio * (1 / max(0.001, (1.1 - ratio)))

def trouver_meilleur_job(poste, jobs_dispos, matrice_duree):
    # Filtrage par type de véhicule (v_type)
    candidats = [j for j in jobs_dispos if j.v_type == poste.vehicule_type]
    if not candidats: return None
    
    candidats.sort(key=lambda x: x.score_stress, reverse=True)
    top_candidats = candidats[:5]
    
    # 1. Priorité : Même couloir dynamique + déjà sur place
    for j in top_candidats:
        if sont_dans_le_meme_couloir(poste.job_precedent, j) and j.origin == poste.position_actuelle:
            return j
            
    # 2. Priorité : Départ sur place (évite trajet vide)
    for j in top_candidats:
        if j.origin == poste.position_actuelle:
            return j
            
    # 3. Proche voisin géographique
    scored = []
    for idx, j in enumerate(top_candidats):
        dist = matrice_duree.get(poste.position_actuelle, {}).get(j.origin, 999)
        scored.append(((idx + (dist / 10)), j))
    scored.sort(key=lambda x: x[0])
    return scored[0][1]

# =================================================================
# 4. ORDONNANCEMENT
# =================================================================

def ordonnancer_journee(liste_sj, n_max_dict, df_vehicules, matrice_duree, params_logistique):
    pas = 5
    rh_params = params_logistique.get('rh', {})
    h_prise = to_decimal_minutes(rh_params.get('h_prise_min', time(6, 0)))
    h_fin = to_decimal_minutes(rh_params.get('h_fin_max', time(21, 0)))
    
    # Nom de colonne spécifique fourni par l'utilisateur
    nom_col_man = "Temps de mise à quai - manœuvre, contact/admin (minutes)"
    
    postes = []
    for v_type, n_veh in n_max_dict.items():
        if n_veh <= 0: continue
        
        # Accès par la colonne 'Types' (avec un s)
        df_f = df_vehicules[df_vehicules['Types'] == v_type]
        if df_f.empty: continue
        row_v = df_f.iloc[0]
        
        site_depot = row_v['Stationnement initial']
        t_man = row_v[nom_col_man] if nom_col_man in row_v else 10
        
        for i in range(1, int(n_veh) + 1):
            p = PosteChauffeur(f"{v_type}_{i:02d}", v_type, site_depot, rh_params)
            p.t_manoeuvre = t_man
            postes.append(p)

    jobs_restants = [j for j in liste_sj]
    heure_actuelle = h_prise

    while heure_actuelle <= h_fin:
        for p in postes:
            if p.mettre_a_jour(pas):
                sj = p.job_en_cours
                
                if p.etat == 'PRISE_POSTE':
                    p.etat = 'DISPONIBLE'; p.enregistrer_evenement(heure_actuelle, "DISPONIBLE")
                
                elif p.etat == 'EN_TRAJET_VIDE':
                    if sj:
                        p.etat = 'EN_MANOEUVRE_QUAI'; p.temps_restant_etat = p.t_manoeuvre
                        p.position_actuelle = sj.origin
                        p.enregistrer_evenement(heure_actuelle, "EN_MANOEUVRE_QUAI", sj, "Mise à quai")
                    else:
                        p.position_actuelle = p.stationnement_initial; p.etat = 'DISPONIBLE'
                
                elif p.etat == 'EN_MANOEUVRE_QUAI':
                    if sj and p.position_actuelle == sj.origin:
                        p.etat = 'EN_CHARGEMENT'; p.temps_restant_etat = sj.temps_chargement
                        p.enregistrer_evenement(heure_actuelle, "EN_CHARGEMENT", sj)
                    else:
                        p.etat = 'EN_DECHARGEMENT'; p.temps_restant_etat = sj.temps_dechargement
                        p.enregistrer_evenement(heure_actuelle, "EN_DECHARGEMENT", sj)
                
                elif p.etat == 'EN_CHARGEMENT':
                    p.etat = 'EN_TRAJET_PLEIN'
                    dist = matrice_duree.get(sj.origin, {}).get(sj.destination, 30)
                    p.temps_restant_etat = dist
                    p.enregistrer_evenement(heure_actuelle, "EN_TRAJET_PLEIN", sj)
                
                elif p.etat == 'EN_TRAJET_PLEIN':
                    p.etat = 'EN_MANOEUVRE_QUAI'; p.temps_restant_etat = p.t_manoeuvre
                    p.position_actuelle = sj.destination
                    p.enregistrer_evenement(heure_actuelle, "EN_MANOEUVRE_QUAI", sj, "Mise à quai")
                
                elif p.etat == 'EN_DECHARGEMENT':
                    if heure_actuelle > sj.h_deadline_min:
                        return {"succes": False, "erreur": f"Retard sur {sj.flux_id}"}
                    p.etat = 'DISPONIBLE'; p.job_precedent = sj; p.job_en_cours = None
                    p.enregistrer_evenement(heure_actuelle, "DISPONIBLE")
                
                elif p.etat == 'EN_PAUSE':
                    p.etat = 'DISPONIBLE'; p.enregistrer_evenement(heure_actuelle, "DISPONIBLE")

        # Affectation
        jobs_dispos = [j for j in jobs_restants if j.h_dispo_min <= heure_actuelle]
        for j in jobs_dispos: j.score_stress = calculer_score_stress(j, heure_actuelle)

        for p in postes:
            if p.etat == 'INACTIF':
                if any(j.v_type == p.vehicule_type for j in jobs_dispos):
                    p.etat = 'PRISE_POSTE'; p.temps_restant_etat = 15; p.enregistrer_evenement(heure_actuelle, "PRISE_POSTE")
            elif p.est_disponible():
                if p.verifier_fin_service() or p.verifier_besoin_pause():
                    if p.position_actuelle == p.stationnement_initial:
                        if p.verifier_fin_service():
                            p.etat = 'FIN_POSTE'; p.enregistrer_evenement(heure_actuelle, "FIN_POSTE")
                        else:
                            p.etat = 'EN_PAUSE'; p.temps_restant_etat = p.duree_pause
                            p.is_pause_faite = True; p.enregistrer_evenement(heure_actuelle, "EN_PAUSE")
                    else:
                        dist_depot = matrice_duree.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                        p.etat = 'EN_TRAJET_VIDE'; p.temps_restant_etat = dist_depot
                        p.enregistrer_evenement(heure_actuelle, "EN_TRAJET_VIDE", details="Retour Dépôt")
                else:
                    job = trouver_meilleur_job(p, jobs_dispos, matrice_duree)
                    if job:
                        p.job_en_cours = job; jobs_restants.remove(job); jobs_dispos.remove(job)
                        dist_approche = matrice_duree.get(p.position_actuelle, {}).get(job.origin, 0)
                        if dist_approche > 0:
                            p.etat = 'EN_TRAJET_VIDE'; p.temps_restant_etat = dist_approche
                            p.enregistrer_evenement(heure_actuelle, "EN_TRAJET_VIDE", job)
                        else:
                            p.etat = 'EN_MANOEUVRE_QUAI'; p.temps_restant_etat = p.t_manoeuvre
                            p.enregistrer_evenement(heure_actuelle, "EN_MANOEUVRE_QUAI", job)
        heure_actuelle += pas

    return {"succes": len(jobs_restants) == 0, "postes": postes, "reliquat": len(jobs_restants)}

# =================================================================
# 5. ITERATION
# =================================================================

def trouver_meilleure_configuration_journee(liste_sj, intensite_par_type, df_vehicules, matrice_duree, params_logistique):
    n_max_initial = { v_type: math.ceil(max(intensites) * 1.2) for v_type, intensites in intensite_par_type.items() }
    res = ordonnancer_journee(liste_sj, n_max_initial, df_vehicules, matrice_duree, params_logistique)
    if res["succes"]:
        return res
    else:
        n_max_safe = {v: count + 1 for v, count in n_max_initial.items()}
        return ordonnancer_journee(liste_sj, n_max_safe, df_vehicules, matrice_duree, params_logistique)
