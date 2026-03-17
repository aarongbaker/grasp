import { useAuth } from '../context/AuthContext';
import { Skeleton } from '../components/shared/Skeleton';
import styles from './ProfilePage.module.css';

export function ProfilePage() {
  const { user } = useAuth();

  if (!user) {
    return (
      <div>
        <Skeleton variant="heading" />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-md)', marginTop: 'var(--space-xl)' }}>
          <Skeleton variant="card" count={2} />
        </div>
      </div>
    );
  }

  const kc = user.kitchen_config;

  return (
    <div>
      <h1 className={styles.title}>Your Kitchen</h1>

      <div className={styles.section}>
        <h2 className={styles.sectionTitle}>Profile</h2>
        <div className={styles.grid}>
          <div className={styles.field}>
            <div className={styles.fieldLabel}>Name</div>
            <div className={styles.fieldValue}>{user.name}</div>
          </div>
          <div className={styles.field}>
            <div className={styles.fieldLabel}>Email</div>
            <div className={styles.fieldValue}>{user.email}</div>
          </div>
        </div>
      </div>

      {kc && (
        <div className={styles.section}>
          <h2 className={styles.sectionTitle}>Kitchen Config</h2>
          <div className={styles.grid}>
            <div className={styles.field}>
              <div className={styles.fieldLabel}>Burners</div>
              <div className={styles.fieldValueMono}>{kc.max_burners}</div>
            </div>
            <div className={styles.field}>
              <div className={styles.fieldLabel}>Oven Racks</div>
              <div className={styles.fieldValueMono}>{kc.max_oven_racks}</div>
            </div>
            <div className={styles.field}>
              <div className={styles.fieldLabel}>Second Oven</div>
              <div className={styles.fieldValue}>{kc.has_second_oven ? 'Yes' : 'No'}</div>
            </div>
          </div>
        </div>
      )}

      {user.dietary_defaults.length > 0 && (
        <div className={styles.section}>
          <h2 className={styles.sectionTitle}>Dietary Defaults</h2>
          <div className={styles.dietaryList}>
            {user.dietary_defaults.map((d) => (
              <span key={d} className={styles.dietaryTag}>{d}</span>
            ))}
          </div>
        </div>
      )}

      <div className={styles.section}>
        <h2 className={styles.sectionTitle}>Equipment</h2>
        {user.equipment.length === 0 ? (
          <div className={styles.emptyEquipment}>No equipment registered yet.</div>
        ) : (
          <div className={styles.equipmentList}>
            {user.equipment.map((eq) => (
              <div key={eq.equipment_id} className={styles.equipmentItem}>
                <span className={styles.equipmentName}>{eq.name}</span>
                <span className={styles.equipmentCategory}>{eq.category}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
