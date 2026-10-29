


    // !!! This file is generated using emlearn !!!

    #include <stdint.h>
    

static inline int32_t dt_r4_d6_fp32_tree_0(const float *features, int32_t features_length) {
          if (features[0] < 0.480359f) {
              if (features[1] < 0.079693f) {
                  if (features[1] < -0.990309f) {
                      return 0;
                  } else {
                      if (features[5] < 0.480248f) {
                          if (features[1] < -0.694907f) {
                              if (features[0] < -0.845350f) {
                                  return 6;
                              } else {
                                  return 8;
                              }
                          } else {
                              if (features[7] < -0.698001f) {
                                  return 2;
                              } else {
                                  return 5;
                              }
                          }
                      } else {
                          if (features[22] < 0.040932f) {
                              if (features[23] < 0.565158f) {
                                  return 5;
                              } else {
                                  return 4;
                              }
                          } else {
                              if (features[21] < -0.237569f) {
                                  return 5;
                              } else {
                                  return 4;
                              }
                          }
                      }
                  }
              } else {
                  if (features[17] < -1.021069f) {
                      return 8;
                  } else {
                      return 1;
                  }
              }
          } else {
              if (features[26] < 1.251644f) {
                  if (features[21] < 0.568489f) {
                      return 9;
                  } else {
                      return 7;
                  }
              } else {
                  return 3;
              }
          }
        }
        

int32_t dt_r4_d6_fp32_predict(const float *features, int32_t features_length) {

        int32_t votes[10] = {0,};
        int32_t _class = -1;

        _class = dt_r4_d6_fp32_tree_0(features, features_length); votes[_class] += 1;
    
        int32_t most_voted_class = -1;
        int32_t most_voted_votes = 0;
        for (int32_t i=0; i<10; i++) {

            if (votes[i] > most_voted_votes) {
                most_voted_class = i;
                most_voted_votes = votes[i];
            }
        }
        return most_voted_class;
    }
    

int dt_r4_d6_fp32_predict_proba(const float *features, int32_t features_length, float *out, int out_length) {

        int32_t _class = -1;

        for (int i=0; i<out_length; i++) {
            out[i] = 0.0f;
        }

        _class = dt_r4_d6_fp32_tree_0(features, features_length); out[_class] += 1.0f;
    
        // compute mean
        for (int i=0; i<out_length; i++) {
            out[i] = out[i] / 1;
        }
        return 0;
    }
    