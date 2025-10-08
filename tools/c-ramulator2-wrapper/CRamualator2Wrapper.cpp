#include "./CRamualator2Wrapper.h"


void CRamualator2Wrapper::init(const std::string& config_path){
    YAML::Node config = Ramulator::Config::parse_config_file(config_path, {});
    ramulator2_frontend = Ramulator::Factory::create_frontend(config);
    ramulator2_memorysystem = Ramulator::Factory::create_memory_system(config);

    ramulator2_frontend->connect_memory_system(ramulator2_memorysystem);
    ramulator2_memorysystem->connect_frontend(ramulator2_frontend);
}

float CRamualator2Wrapper::get_memory_tCK() const {
    return ramulator2_memorysystem->get_tCK();
}


bool CRamualator2Wrapper::send_request(int64_t addr, bool is_write, std::function<void(Ramulator::Request&)> callback) {
    bool enqueue_success;
    enqueue_success = ramulator2_frontend->receive_external_requests(is_write, addr, 0, callback);
    return enqueue_success;
}

void CRamualator2Wrapper::finish(){
    ramulator2_frontend->finalize();
    ramulator2_memorysystem->finalize();
}

void CRamualator2Wrapper::frontend_tick(){
    ramulator2_frontend->tick();
}

void CRamualator2Wrapper::memory_system_tick(){
    ramulator2_memorysystem->tick();
}

CRamualator2Wrapper::~CRamualator2Wrapper() {
    if(ramulator2_frontend) {
        delete ramulator2_frontend;
        ramulator2_frontend = nullptr;
    }
    if(ramulator2_memorysystem) {
        delete ramulator2_memorysystem;
        ramulator2_memorysystem = nullptr;
    }
}

extern "C" {

    // Factory: create a new MyWrapper instance
    CRamualator2Wrapper* dram_new() {
        return new CRamualator2Wrapper();
    }
    
    // Destructor: delete a MyWrapper instance
    void dram_delete(CRamualator2Wrapper* obj) {
        delete obj;
    }
    
    // Wrap init method: pass config path as C string
    void dram_init(CRamualator2Wrapper* obj, const char* config_path) {
        obj->init(std::string(config_path));
    }
    
    // Wrap get_memory_tCK method
    float get_memory_tCK(CRamualator2Wrapper* obj) {
        return obj->get_memory_tCK();
    }
    
    // Wrap send_request method
    bool send_request(CRamualator2Wrapper* obj, int64_t addr, bool is_write, void (*callback)(Ramulator::Request*, void*), void* ctx) {
        return obj->send_request(addr, is_write, 
            [callback, ctx](Ramulator::Request& req) {
                callback(&req, ctx);
            });
    }
    
    // Wrap finish method
    void finish(CRamualator2Wrapper* obj) {
        obj->finish();
    }
    
    // Wrap tick method
    void frontend_tick(CRamualator2Wrapper* obj) {
        obj->frontend_tick();
    }

    void memory_system_tick(CRamualator2Wrapper* obj) {
        obj->memory_system_tick();
    }
    
}
